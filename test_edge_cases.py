#!/usr/bin/env python3
"""
Manual edge-case checks for command validation.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dynamic_modules import (
    DynamicModule,
    DynamicModuleHost,
    _render_command,
    _sanitize_value,
    _validate_and_sanitize_context,
    _validate_command_template,
)


def expect_failure(label: str, func, *args) -> None:
    try:
        func(*args)
    except ValueError as exc:
        print(f"  ok  {label}: {exc}")
        return
    raise AssertionError(f"{label} should have failed")


def main() -> int:
    print("Testing Unicode preservation")
    for value in ["café", "บริการ", "München"]:
        result = _sanitize_value(value)
        print(f"  ok  {value!r} -> {result!r}")

    print("\nTesting unsafe value rejection")
    for value in ["test;rm -rf /", "value`whoami`", "bad|pipe", "bad\tvalue"]:
        expect_failure(value, _sanitize_value, value)

    print("\nTesting template validation")
    for command in [
        "ps aux | grep {{process_name}}",
        "export ORACLE_SID={{instance_name}}; echo 'ok' | sqlplus -S / as sysdba",
        "[ \"{{check_docker}}\" = \"true\" ] && docker ps || echo 'skip'",
    ]:
        _validate_command_template(command)
        print(f"  ok  {command!r}")

    for command in ["$(whoami)", "sudo id", "echo test > /dev/sda", "echo ok\nrm -rf /"]:
        expect_failure(command, _validate_command_template, command)

    print("\nTesting render path")
    module = DynamicModule(
        id=1,
        name="test_module",
        system_name="TestSystem",
        enabled=True,
        timezone="UTC",
        daily_time="08:00",
        recipient_emails=[],
    )
    host = DynamicModuleHost(
        id=1,
        name="test_host",
        ip="192.168.1.100",
        variables={"service_name": "nginx", "instance_name": "DB01"},
    )
    rendered = _render_command("systemctl status {{service_name}}; echo {{instance_name}}", module, host)
    print(f"  ok  rendered -> {rendered!r}")

    print("\nTesting context error reporting")
    expect_failure(
        "dangerous context",
        _validate_and_sanitize_context,
        {"service_name": "nginx;rm -rf /"},
    )

    print("\nEdge-case checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
