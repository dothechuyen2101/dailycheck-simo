"""
Tests for command validation and template value handling in dynamic modules.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.dynamic_modules import (
    DynamicModule,
    DynamicModuleHost,
    _render_command,
    _sanitize_value,
    _validate_and_sanitize_context,
    _validate_command_template,
)


def test_sanitize_value_preserves_safe_unicode():
    assert _sanitize_value("cafe") == "cafe"
    assert _sanitize_value("café") == "café"
    assert _sanitize_value("บริการ") == "บริการ"
    assert _sanitize_value("/var/log/app-01") == "/var/log/app-01"
    assert _sanitize_value("svc_name@example.com") == "svc_name@example.com"
    assert _sanitize_value("") == ""
    assert _sanitize_value(None) == ""


def test_sanitize_value_rejects_shell_metacharacters():
    for value in [
        "test;rm -rf /",
        "value`whoami`",
        "test$sudo",
        'test"quote"',
        "test'quote'",
        "test&command",
        "test|command",
        "name\twith-tab",
    ]:
        with pytest.raises(ValueError, match="disallowed|unsupported"):
            _sanitize_value(value)


def test_validate_command_template_allows_legitimate_shell_syntax():
    safe_commands = [
        "date",
        "hostname",
        "df -k",
        "systemctl status {{service_name}}",
        "ls -la /var/log",
        "echo 'test message'",
        "cat /proc/cpuinfo",
        "ps aux | grep {{process_name}}",
        "export ORACLE_SID={{instance_name}}; echo 'ok' | sqlplus -S / as sysdba",
        "[ \"{{is_namenode}}\" = \"true\" ] && hdfs dfsadmin -report || echo 'SKIPPED'",
        "tail -100 /tmp/app.log 2>/dev/null | grep ERROR",
    ]

    for cmd in safe_commands:
        _validate_command_template(cmd)


def test_validate_command_template_rejects_dangerous_patterns():
    dangerous_cases = [
        ("$(rm -rf /)", "command substitution"),
        ("`rm -rf /`", "backtick"),
        ("sudo rm -rf /", "sudo"),
        ("su - root", "su"),
        ("rm -rf /", "root deletion"),
        ("echo test > /dev/sda", "device files"),
        ("echo ok\nrm -rf /", "control characters"),
    ]

    for command, expected_message in dangerous_cases:
        with pytest.raises(ValueError, match=expected_message):
            _validate_command_template(command)


def test_validate_and_sanitize_context_reports_which_variable_failed():
    context = {
        "safe_var": "safe_value",
        "unicode_var": "café",
        "dangerous_var": "dangerous;rm -rf /",
    }

    with pytest.raises(ValueError, match="dangerous_var"):
        _validate_and_sanitize_context(context)


def test_render_command_safe():
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
        variables={"service_name": "nginx", "path": "/var/log"},
    )

    rendered = _render_command("systemctl status {{service_name}} && ls {{path}}", module, host)
    assert rendered == "systemctl status nginx && ls /var/log"


def test_render_command_rejects_dangerous_context_values():
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
        variables={"service_name": "nginx;rm -rf /"},
    )

    with pytest.raises(ValueError, match="service_name"):
        _render_command("systemctl status {{service_name}}", module, host)


def test_render_command_rejects_missing_variables():
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
        variables={},
    )

    with pytest.raises(ValueError, match="Missing template variable"):
        _render_command("systemctl status {{service_name}}", module, host)
