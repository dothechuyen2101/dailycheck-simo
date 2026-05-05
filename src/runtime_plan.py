"""
Runtime Plan Builder – Xây dựng kế hoạch thực thi từ dữ liệu Dashboard nhập
(systems, hosts, plan_steps) trong SQLite, có validate theo check_registry.
"""
from __future__ import annotations

from typing import Optional

from src.check_registry import validate_step_params
from src.plan_store import PlanStore


class PlanValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors) if errors else "Plan không hợp lệ")
        self.errors = errors


def _apply_host_scope(all_hosts: list[dict], scope: dict) -> list[dict]:
    mode = (scope or {}).get("mode", "all")
    if mode == "all":
        return all_hosts
    if mode == "selected":
        host_ids = set(int(x) for x in (scope.get("host_ids") or []))
        if not host_ids:
            return []
        return [h for h in all_hosts if int(h["id"]) in host_ids]
    # Fallback: chạy trên host đầu tiên
    if mode == "first":
        return all_hosts[:1] if all_hosts else []
    return all_hosts


def _simplify_host(host: dict) -> dict:
    """Chuẩn hoá host cho runtime (loại field nội bộ)."""
    return {
        "id": host.get("id"),
        "name": host.get("name"),
        "ip": host.get("ip"),
        "username": host.get("username"),
        "password": host.get("password"),
        "key_path": host.get("key_path"),
        "ssh_port": host.get("ssh_port", 22),
        "role": host.get("role"),
        "extra": host.get("extra") or {},
        "enabled": bool(host.get("enabled", 1)),
    }


def build_runtime_plan(
    plan_id: Optional[int] = None,
    store: Optional[PlanStore] = None,
) -> dict:
    """Load plan từ DB, validate và trả về runtime plan.

    Cấu trúc trả về::

        {
          "plan_id": int,
          "plan_name": str,
          "systems": [
            {
              "system_id": int,
              "system_name": str,
              "system_type": str,
              "hosts": [ {...} ],
              "steps": [
                {"step_id": str, "step_name": str, "order": int,
                 "host_scope": {...}, "params": {...}, "targets": [host,...]}
              ]
            }
          ]
        }
    """
    store = store or PlanStore()

    if plan_id is None:
        plan = store.get_default_plan()
        if not plan:
            raise PlanValidationError([
                "Chưa có plan nào trong hệ thống. Hãy tạo plan trên Dashboard."
            ])
    else:
        plan = store.get_plan(plan_id)
        if not plan:
            raise PlanValidationError([f"Không tìm thấy plan id={plan_id}"])
        if not plan.get("enabled"):
            raise PlanValidationError([f"Plan '{plan['name']}' đang bị tắt"])

    all_steps = store.list_plan_steps(plan["id"])
    if not all_steps:
        raise PlanValidationError([
            f"Plan '{plan['name']}' chưa có bước check nào. Hãy thêm step trên Dashboard."
        ])

    systems_cache: dict[int, dict] = {}
    hosts_cache: dict[int, list[dict]] = {}

    def _load_system(system_id: int) -> dict:
        if system_id not in systems_cache:
            sys_row = store.get_system(system_id)
            if not sys_row:
                raise PlanValidationError([f"System id={system_id} không tồn tại"])
            systems_cache[system_id] = sys_row
            hosts = [
                _simplify_host(h)
                for h in store.list_hosts(system_id)
                if h.get("enabled", 1)
            ]
            hosts_cache[system_id] = hosts
        return systems_cache[system_id]

    errors: list[str] = []
    grouped: dict[int, dict] = {}

    for step in all_steps:
        if not step.get("enabled", 1):
            continue

        system_id = int(step["system_id"])
        try:
            system = _load_system(system_id)
        except PlanValidationError as e:
            errors.extend(e.errors)
            continue

        if not system.get("enabled", 1):
            continue

        host_list = hosts_cache.get(system_id, [])
        targets = _apply_host_scope(host_list, step.get("host_scope"))

        step_errors = validate_step_params(
            system["system_type"], step["step_id"], step.get("params") or {}
        )
        if step_errors:
            errors.extend([
                f"[{system['name']} / {step['step_id']}] {e}" for e in step_errors
            ])

        if not targets:
            errors.append(
                f"[{system['name']} / {step['step_id']}] Không có host nào phù hợp với host_scope"
            )

        group = grouped.setdefault(
            system_id,
            {
                "system_id": system_id,
                "system_name": system["name"],
                "system_type": system["system_type"],
                "hosts": host_list,
                "steps": [],
            },
        )
        group["steps"].append(
            {
                "step_row_id": step.get("id"),
                "step_id": step["step_id"],
                "step_name": step.get("step_name") or step["step_id"],
                "order": int(step.get("step_order", 0)),
                "host_scope": step.get("host_scope") or {"mode": "all"},
                "params": step.get("params") or {},
                "targets": targets,
            }
        )

    for group in grouped.values():
        group["steps"].sort(key=lambda s: (s["order"], s["step_row_id"] or 0))

    if errors:
        raise PlanValidationError(errors)

    return {
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "systems": list(grouped.values()),
    }


def serialize_for_snapshot(runtime_plan: dict) -> dict:
    """Loại bỏ password khỏi snapshot trước khi lưu."""
    import copy

    snapshot = copy.deepcopy(runtime_plan)
    for system in snapshot.get("systems", []):
        for host in system.get("hosts", []):
            if "password" in host:
                host["password"] = "***" if host["password"] else None
        for step in system.get("steps", []):
            for host in step.get("targets", []):
                if "password" in host:
                    host["password"] = "***" if host["password"] else None
    return snapshot
