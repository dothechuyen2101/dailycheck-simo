"""
Helper dùng chung cho các module check built-in.
Các hàm *_check trả về (status, message, raw_output, details) để mỗi module tự tạo CheckResult.
"""
import re
from typing import Iterable, List, Tuple

from src.builtin_commands import BuiltinCommandSet
from src.config_manager import get

SQL_ERROR_RE = re.compile(r"(?:ORA|SP2)-\d+[^\n]*")
SYSTEMD_ACTIVE_RE = re.compile(r"^\s*Active:\s*(.+?)\s*$", re.MULTILINE)

# Luôn bỏ qua: ổ CD và các mount nội bộ của Docker (overlay2 của container, shm, netns) –
# chúng chỉ lặp lại dung lượng của phân vùng chứa /var/lib/docker.
DEFAULT_EXCLUDED_MOUNTS = (
    "/cdrom",
    "/cdroom",
    "/var/lib/docker/overlay2",
    "/var/lib/docker/containers",
    "/run/docker",
)
# Tên cột % sử dụng: Linux (Use%), AIX df -k (%Used), POSIX df -P / Solaris (Capacity)
USE_PCT_HEADERS = {"use%", "%used", "used%", "capacity"}

CheckOutcome = Tuple[str, str, str, dict]


def _is_excluded(mount: str, excluded: Iterable[str]) -> bool:
    mount = mount.lower()
    for entry in excluded:
        base = str(entry).lower().rstrip("/")
        if base and (mount == base or mount.startswith(base + "/")):
            return True
    return False


def parse_df_issues(output: str, threshold: float, excluded_mounts: Iterable[str] = ()) -> List[str]:
    """
    Trả về các phân vùng có % sử dụng >= threshold.

    Vị trí cột % sử dụng và Mounted on lấy từ dòng tiêu đề, nên đúng với df -k trên AIX
    (Free %Used Iused %Iused), df -k/-h/-T/-P trên Linux. Dòng bị ngắt khi tên device quá dài
    được ghép lại. Không có tiêu đề thì dùng 2 cột cuối.
    Mount trong DEFAULT_EXCLUDED_MOUNTS và excluded_mounts (kể cả mount con) được bỏ qua.
    """
    excluded = list(DEFAULT_EXCLUDED_MOUNTS) + list(excluded_mounts or [])
    use_idx = mount_idx = None
    pending_device = None
    issues = []
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        lowered = [p.lower() for p in parts]
        if mount_idx is None and "mounted" in lowered:
            mount_idx = lowered.index("mounted")
            use_idx = next((i for i, p in enumerate(lowered) if p in USE_PCT_HEADERS), None)
            continue
        if len(parts) == 1:
            # Tên device quá dài, các cột còn lại nằm ở dòng sau
            pending_device = parts[0]
            continue
        if pending_device is not None and line[:1].isspace():
            parts = [pending_device] + parts
        pending_device = None

        if use_idx is not None and len(parts) > mount_idx:
            pct_str, mount = parts[use_idx], " ".join(parts[mount_idx:])
        elif parts[-2].endswith("%"):
            pct_str, mount = parts[-2], parts[-1]
        else:
            continue
        if _is_excluded(mount, excluded):
            continue
        try:
            pct = int(pct_str.rstrip("%"))
        except ValueError:
            continue
        if pct >= threshold:
            issues.append(f"{mount} ({pct}%)")
    return issues


def sql_errors(output: str) -> List[str]:
    """Các dòng lỗi ORA-/SP2- trong output sqlplus."""
    return SQL_ERROR_RE.findall(output)


def system_info_check(client, commands: BuiltinCommandSet, step_key: str, ctx: dict) -> CheckOutcome:
    res_date = commands.run(client, step_key, "date", ctx)
    res_host = commands.run(client, step_key, "hostname", ctx)
    failed = next((r for r in (res_date, res_host) if not r.success), None)
    if failed:
        return "ERROR", f"Không lấy được date/hostname: {failed.stderr[:200]}", "", {}
    return (
        "OK",
        f"date={res_date.stdout.strip()} | hostname={res_host.stdout.strip()}",
        f"date: {res_date.stdout}\nhostname: {res_host.stdout}",
        {},
    )


def disk_space_check(client, commands: BuiltinCommandSet, step_key: str, ctx: dict) -> CheckOutcome:
    threshold = get("thresholds.disk_usage_max_pct", 90)
    res = commands.run(client, step_key, "main", ctx)
    if not res.success:
        return "ERROR", f"Lệnh {res.command} thất bại: {res.stderr[:200]}", "", {}
    issues = parse_df_issues(res.stdout, threshold, get("thresholds.disk_exclude_mounts", []) or [])
    if issues:
        return (
            "CRITICAL",
            f"Phân vùng vượt {threshold}%: {', '.join(issues)}",
            res.stdout,
            {"overloaded": issues},
        )
    return "OK", f"Tất cả phân vùng < {threshold}%", res.stdout, {}


def _similar_units(client, commands: BuiltinCommandSet, step_key: str, ctx: dict, service_name: str) -> List[str]:
    """Các unit có tên chứa phần đầu của service (vd. wso2am-3.2.0.service → 'wso2')."""
    if not commands.has_command(step_key, "units"):
        return []
    res = commands.run(client, step_key, "units", ctx)
    if not res.success:
        return []
    m = re.match(r"[A-Za-z]+\d*", service_name)
    prefix = (m.group(0) if m else service_name).lower()
    units = [line.split()[0] for line in res.stdout.splitlines() if line.split()]
    return [u for u in units if prefix in u.lower()][:5]


def find_processes(ps_output: str, pattern: str) -> List[Tuple[str, str]]:
    """(PID, dòng lệnh) của tiến trình có dòng lệnh chứa pattern (không phân biệt hoa thường), từ `ps -eo pid,args`."""
    pattern = pattern.lower()
    found = []
    for line in ps_output.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and pattern in parts[1].lower():
            found.append((parts[0], parts[1].strip()))
    return found


def _describe_processes(found: List[Tuple[str, str]]) -> str:
    pid, args = found[0]
    more = f" và {len(found) - 1} tiến trình khác" if len(found) > 1 else ""
    return f"PID {pid}: {args[:80]}{more}"


def _process_pattern(ctx: dict, service_name: str) -> str:
    return ctx.get("process_pattern") or re.sub(r"\.service$", "", service_name)


def service_check(client, commands: BuiltinCommandSet, step_key: str, ctx: dict) -> CheckOutcome:
    service_name = ctx.get("service_name", "")
    res = commands.run(client, step_key, "main", ctx)
    if res.exit_code < 0:
        # Lệnh không chạy được (lệnh không hợp lệ, mất kết nối, timeout)
        return "ERROR", f"Không chạy được lệnh {res.command}: {res.stderr[:200]}", "", {}
    output = f"{res.stdout}\n{res.stderr}".strip()
    lower = output.lower()
    if "could not be found" in lower or "loaded: not-found" in lower or res.exit_code == 4:
        similar = _similar_units(client, commands, step_key, ctx, service_name)
        hint = (f"; service có tên gần giống trên host: {', '.join(similar)}" if similar
                else "; không có systemd unit nào có tên gần giống")
        return (
            "ERROR",
            f"Không tìm thấy service {service_name} trên host – kiểm tra lại tên service trong config{hint}",
            output, {"similar_units": similar},
        )
    active = SYSTEMD_ACTIVE_RE.search(output)
    if active:
        # Đánh giá theo dòng "Active:" để không bị nhầm bởi chữ failed/inactive trong phần log
        state = active.group(1)
        if state.lower().startswith("active (running)"):
            return "OK", f"Service {service_name} ACTIVE (running)", output, {}
        if state.lower().startswith(("inactive", "failed")):
            return "CRITICAL", f"Service {service_name} không chạy: {state}", output, {}
        if state.lower().startswith("active (exited)"):
            # Unit chỉ chạy script khởi động rồi thoát – systemd không biết ứng dụng còn chạy không,
            # nên kiểm tra tiến trình
            pattern = _process_pattern(ctx, service_name)
            res_ps = commands.run(client, step_key, "processes", ctx) if commands.has_command(step_key, "processes") else None
            found = find_processes(res_ps.stdout, pattern) if res_ps and res_ps.success else []
            if found:
                return ("OK", f"Service {service_name} active (exited), tiến trình đang chạy ({_describe_processes(found)})",
                        output, {"pids": [pid for pid, _ in found]})
            return ("WARNING",
                    f"Service {service_name} active (exited) và không thấy tiến trình nào chứa '{pattern}' – "
                    f"kiểm tra thủ công, hoặc khai báo process_pattern trong config nếu tiến trình có tên khác",
                    output, {})
        return "WARNING", f"Service {service_name} trạng thái không xác định: {state}", output, {}
    # Không có dòng "Active:" (output không phải của systemctl status) – dò theo toàn bộ output
    if "active (running)" in lower:
        return "OK", f"Service {service_name} ACTIVE (running)", output, {}
    if "inactive" in lower or "failed" in lower:
        return "CRITICAL", f"Service {service_name} không chạy", output, {}
    return "WARNING", f"Service {service_name} trạng thái không xác định", output, {}


def process_check(client, commands: BuiltinCommandSet, step_key: str, ctx: dict) -> CheckOutcome:
    """Ứng dụng không chạy bằng systemd (service_type: process): tìm tiến trình có dòng lệnh chứa service_name."""
    pattern = ctx.get("service_name", "")
    res = commands.run(client, step_key, "main", ctx)
    if not res.success:
        return "ERROR", f"Lệnh {res.command} thất bại: {res.stderr[:200]}", "", {}
    found = find_processes(res.stdout, pattern)
    if found:
        return (
            "OK",
            f"{len(found)} tiến trình chứa '{pattern}' đang chạy ({_describe_processes(found)})",
            "\n".join(f"{pid} {args}" for pid, args in found),
            {"pids": [pid for pid, _ in found]},
        )
    return "CRITICAL", f"Không có tiến trình nào chứa '{pattern}' đang chạy", "", {}
