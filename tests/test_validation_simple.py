"""
Simple validation test script without pytest dependency.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.dynamic_modules import (
    DynamicModule,
    DynamicModuleHost,
    _render_command,
    _sanitize_value,
    _validate_and_sanitize_context,
    _validate_command_template,
)


def main() -> int:
    assert _sanitize_value("café") == "café"
    assert _sanitize_value("/var/log/app-01") == "/var/log/app-01"

    for value in ["bad;value", "bad`value`", "bad|value"]:
        try:
            _sanitize_value(value)
            raise AssertionError(f"Expected unsafe value to fail: {value}")
        except ValueError:
            pass

    _validate_command_template("ps aux | grep {{process_name}}")
    _validate_command_template("[ \"{{flag}}\" = \"true\" ] && echo ok || echo skip")

    for command in ["$(whoami)", "sudo id", "echo test > /dev/sda"]:
        try:
            _validate_command_template(command)
            raise AssertionError(f"Expected unsafe command to fail: {command}")
        except ValueError:
            pass

    try:
        _validate_and_sanitize_context({"service_name": "nginx;rm -rf /"})
        raise AssertionError("Expected unsafe context value to fail")
    except ValueError as exc:
        assert "service_name" in str(exc)

    module = DynamicModule(
        id=1,
        name="module",
        system_name="system",
        enabled=True,
        timezone="UTC",
        daily_time="08:00",
        recipient_emails=[],
    )
    host = DynamicModuleHost(
        id=1,
        name="host",
        ip="192.168.1.100",
        variables={"service_name": "nginx"},
    )
    assert _render_command("systemctl status {{service_name}}", module, host) == "systemctl status nginx"

    print("All validation checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
