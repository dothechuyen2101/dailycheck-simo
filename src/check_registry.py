"""
Check Registry – Schema cho các system type và step mà Dashboard cho phép người dùng nhập.
Registry chỉ mô tả field; không chứa giá trị nghiệp vụ mặc định dùng cho runtime.
"""
from copy import deepcopy
from typing import Any


# Kiểu field hỗ trợ: text, number, select, list, textarea, json, boolean
CHECK_REGISTRY: dict[str, dict[str, Any]] = {
    "oracle": {
        "label": "Oracle Database",
        "steps": {
            "system_info": {
                "label": "Bước 1 - Thông số hệ thống",
                "params": [
                    {"name": "commands", "label": "Các lệnh thực thi", "type": "list", "required": True,
                     "help": "Mỗi dòng một lệnh, ví dụ: date, hostname"},
                ],
            },
            "disk_space": {
                "label": "Bước 2 - Phân vùng đĩa",
                "params": [
                    {"name": "command", "label": "Lệnh kiểm tra", "type": "text", "required": True,
                     "help": "Ví dụ: df -k hoặc df -kh"},
                    {"name": "threshold_pct", "label": "Ngưỡng %used", "type": "number", "required": True},
                    {"name": "excluded_mounts", "label": "Mount loại trừ", "type": "list", "required": False,
                     "help": "Mỗi dòng một mount, ví dụ: /cdrom"},
                ],
            },
            "crs_status": {
                "label": "Bước 3 - CRS Status",
                "params": [
                    {"name": "command", "label": "Lệnh CRS", "type": "text", "required": True},
                    {"name": "fallback_command", "label": "Fallback command", "type": "text", "required": False},
                    {"name": "allowed_offline", "label": "Resource được phép OFFLINE",
                     "type": "list", "required": False,
                     "help": "Ví dụ: ora.gsd, ora.oc4j, ora....bkup.db"},
                ],
            },
            "ocr": {
                "label": "Bước 4 - OCR Check",
                "params": [
                    {"name": "command", "label": "Lệnh kiểm tra OCR", "type": "text", "required": True},
                    {"name": "expected_contains", "label": "Chuỗi yêu cầu xuất hiện",
                     "type": "list", "required": True},
                ],
            },
            "voting_disk": {
                "label": "Bước 5 - Voting Disk",
                "params": [
                    {"name": "command", "label": "Lệnh kiểm tra", "type": "text", "required": True},
                    {"name": "required_online", "label": "Số voting disk ONLINE yêu cầu",
                     "type": "number", "required": True},
                ],
            },
            "archive_switch": {
                "label": "Bước 6 - Archive Switch",
                "params": [
                    {"name": "oracle_sid_field", "label": "Tên field lấy ORACLE_SID từ host.extra",
                     "type": "text", "required": True,
                     "help": "Ví dụ: oracle_sid"},
                    {"name": "sql", "label": "Lệnh SQL", "type": "textarea", "required": True},
                    {"name": "expected_contains", "label": "Chuỗi yêu cầu xuất hiện",
                     "type": "list", "required": True},
                ],
            },
            "free_tablespace": {
                "label": "Bước 7 - Free Tablespace",
                "params": [
                    {"name": "oracle_sid_field", "label": "Tên field ORACLE_SID", "type": "text", "required": True},
                    {"name": "sql", "label": "SQL kiểm tra tablespace", "type": "textarea", "required": True},
                    {"name": "system_tablespaces", "label": "System tablespace names",
                     "type": "list", "required": True,
                     "help": "Ví dụ: SYSTEM, SYSAUX, EXAMPLE"},
                    {"name": "system_min_free_pct", "label": "System min %Free",
                     "type": "number", "required": True},
                    {"name": "undo_prefix", "label": "Prefix UNDO tablespace", "type": "text", "required": True,
                     "help": "Ví dụ: UNDOTBS"},
                    {"name": "undo_min_total_mb", "label": "UNDO min Total MB",
                     "type": "number", "required": True},
                    {"name": "default_min_free_pct", "label": "Default min %Free",
                     "type": "number", "required": True},
                ],
            },
            "unusable_indexes": {
                "label": "Bước 8 - Unusable Indexes",
                "params": [
                    {"name": "oracle_sid_field", "label": "Tên field ORACLE_SID", "type": "text", "required": True},
                    {"name": "sql", "label": "SQL kiểm tra", "type": "textarea", "required": True},
                    {"name": "expected_empty_marker", "label": "Chuỗi báo rỗng",
                     "type": "text", "required": True,
                     "help": "Ví dụ: no rows selected"},
                ],
            },
            "invalid_objects": {
                "label": "Bước 9 - Invalid Objects",
                "params": [
                    {"name": "oracle_sid_field", "label": "Tên field ORACLE_SID", "type": "text", "required": True},
                    {"name": "sql", "label": "SQL kiểm tra", "type": "textarea", "required": True},
                    {"name": "expected_empty_marker", "label": "Chuỗi báo rỗng",
                     "type": "text", "required": True},
                ],
            },
            "asm_diskgroup": {
                "label": "Bước 10 - ASM Disk Group",
                "params": [
                    {"name": "asm_sid_field", "label": "Tên field ASM SID từ host.extra",
                     "type": "text", "required": True,
                     "help": "Ví dụ: asm_sid"},
                    {"name": "sql", "label": "SQL kiểm tra ASM", "type": "textarea", "required": True},
                    {"name": "threshold_pct", "label": "Ngưỡng %used", "type": "number", "required": True},
                ],
            },
            "alert_log": {
                "label": "Bước 11 - Alert Log",
                "params": [
                    {"name": "oracle_sid_field", "label": "Tên field ORACLE_SID", "type": "text", "required": True},
                    {"name": "path_sql", "label": "SQL lấy đường dẫn alert log",
                     "type": "textarea", "required": True},
                    {"name": "path_field", "label": "Tên field trong output",
                     "type": "text", "required": True,
                     "help": "Ví dụ: background_dump_dest"},
                    {"name": "tail_command_template", "label": "Template lệnh quét log",
                     "type": "text", "required": True,
                     "help": "Dùng {alert_dir}, {instance}, {keywords}"},
                    {"name": "keywords", "label": "Keywords lỗi", "type": "list", "required": True},
                ],
            },
        },
    },
    "mongodb": {
        "label": "MongoDB",
        "steps": {
            "system_info": {
                "label": "Bước 1 - Thông số hệ thống",
                "params": [
                    {"name": "commands", "label": "Các lệnh", "type": "list", "required": True},
                ],
            },
            "disk_space": {
                "label": "Bước 2 - Phân vùng đĩa",
                "params": [
                    {"name": "command", "label": "Lệnh kiểm tra", "type": "text", "required": True},
                    {"name": "threshold_pct", "label": "Ngưỡng %used", "type": "number", "required": True},
                    {"name": "excluded_mounts", "label": "Mount loại trừ", "type": "list", "required": False},
                ],
            },
            "service_status": {
                "label": "Bước 3 - Trạng thái service MongoDB",
                "params": [
                    {"name": "command", "label": "Lệnh systemctl", "type": "text", "required": True,
                     "help": "Ví dụ: systemctl status mongod"},
                    {"name": "expected_contains", "label": "Chuỗi yêu cầu",
                     "type": "list", "required": True,
                     "help": "Ví dụ: active (running)"},
                ],
            },
        },
    },
    "hadoop": {
        "label": "Hadoop",
        "steps": {
            "system_info": {
                "label": "Bước 1 - Thông số hệ thống",
                "params": [
                    {"name": "commands", "label": "Các lệnh", "type": "list", "required": True},
                ],
            },
            "disk_space": {
                "label": "Bước 2 - Phân vùng đĩa",
                "params": [
                    {"name": "command", "label": "Lệnh kiểm tra", "type": "text", "required": True},
                    {"name": "threshold_pct", "label": "Ngưỡng %used", "type": "number", "required": True},
                    {"name": "excluded_mounts", "label": "Mount loại trừ", "type": "list", "required": False},
                ],
            },
            "hdfs_report": {
                "label": "Bước 3 - HDFS dfsadmin report",
                "params": [
                    {"name": "command", "label": "Lệnh HDFS", "type": "text", "required": True,
                     "help": "Ví dụ: hdfs dfsadmin -report"},
                    {"name": "safe_mode_error", "label": "Mẫu Safe Mode báo lỗi",
                     "type": "text", "required": True,
                     "help": "Ví dụ: safe mode is ON"},
                    {"name": "min_live_datanodes", "label": "Số live DataNode tối thiểu",
                     "type": "number", "required": True},
                    {"name": "dfs_used_threshold_pct", "label": "Ngưỡng DFS Used %",
                     "type": "number", "required": True},
                ],
            },
        },
    },
    "application": {
        "label": "Application / Service",
        "steps": {
            "system_info": {
                "label": "Bước 1 - Thông số hệ thống",
                "params": [
                    {"name": "commands", "label": "Các lệnh", "type": "list", "required": True},
                ],
            },
            "disk_space": {
                "label": "Bước 2 - Phân vùng đĩa",
                "params": [
                    {"name": "command", "label": "Lệnh kiểm tra", "type": "text", "required": True},
                    {"name": "threshold_pct", "label": "Ngưỡng %used", "type": "number", "required": True},
                    {"name": "excluded_mounts", "label": "Mount loại trừ", "type": "list", "required": False},
                ],
            },
            "service_status": {
                "label": "Bước 3 - Service Status",
                "params": [
                    {"name": "command", "label": "Lệnh kiểm tra service", "type": "text", "required": True,
                     "help": "Ví dụ: systemctl status wso2am-3.2.0.service"},
                    {"name": "expected_contains", "label": "Chuỗi yêu cầu",
                     "type": "list", "required": True},
                ],
            },
            "docker_containers": {
                "label": "Bước 3b - Docker containers",
                "params": [
                    {"name": "ps_command", "label": "Lệnh docker ps", "type": "text", "required": True,
                     "help": "Ví dụ: docker ps --format '{{.Names}}\\t{{.Status}}'"},
                    {"name": "running_marker", "label": "Chuỗi đánh dấu container running",
                     "type": "text", "required": True,
                     "help": "Ví dụ: Up"},
                    {"name": "service_command", "label": "Lệnh docker service ls (optional)",
                     "type": "text", "required": False},
                ],
            },
        },
    },
}


def list_system_types() -> list[dict]:
    return [
        {"system_type": key, "label": meta["label"]}
        for key, meta in CHECK_REGISTRY.items()
    ]


def get_registry() -> dict[str, dict]:
    return deepcopy(CHECK_REGISTRY)


def get_system_type(system_type: str) -> dict:
    if system_type not in CHECK_REGISTRY:
        raise ValueError(f"Unsupported system_type: {system_type}")
    return deepcopy(CHECK_REGISTRY[system_type])


def get_step_schema(system_type: str, step_id: str) -> dict:
    steps = get_system_type(system_type).get("steps", {})
    if step_id not in steps:
        raise ValueError(f"Unsupported step_id '{step_id}' for system_type '{system_type}'")
    return steps[step_id]


def validate_step_params(system_type: str, step_id: str, params: dict) -> list[str]:
    """Trả về danh sách lỗi; rỗng nếu hợp lệ."""
    errors: list[str] = []
    try:
        schema = get_step_schema(system_type, step_id)
    except ValueError as e:
        return [str(e)]

    for field in schema.get("params", []):
        name = field["name"]
        required = field.get("required", False)
        ftype = field.get("type", "text")
        value = (params or {}).get(name)

        if required and (value is None or value == "" or value == []):
            errors.append(f"Thiếu tham số '{name}' ({field.get('label', name)})")
            continue

        if value is None or value == "":
            continue

        if ftype == "number":
            try:
                float(value)
            except (TypeError, ValueError):
                errors.append(f"Tham số '{name}' phải là số")
        elif ftype == "list":
            if not isinstance(value, list):
                errors.append(f"Tham số '{name}' phải là danh sách")
        elif ftype == "boolean":
            if not isinstance(value, bool):
                errors.append(f"Tham số '{name}' phải là true/false")

    return errors
