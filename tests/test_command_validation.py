"""
Tests for command validation and sanitization in dynamic modules.
"""
import pytest
import sys
import os

# Add the src directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.dynamic_modules import (
    _sanitize_value,
    _validate_command_template,
    _validate_and_sanitize_context,
    _render_command,
    DynamicModule,
    DynamicModuleHost
)


def test_sanitize_value():
    """Test value sanitization removes dangerous characters."""
    # Test dangerous characters are removed
    assert _sanitize_value("test;rm -rf /") == "test rm -rf "
    assert _sanitize_value("value`whoami`") == "valuewhoami"
    assert _sanitize_value("test$sudo") == "testsudo"
    assert _sanitize_value('test"quote"') == "testquote"
    assert _sanitize_value("test'quote'") == "testquote"
    assert _sanitize_value("test&command") == "testcommand"
    assert _sanitize_value("test|command") == "testcommand"
    
    # Test allowed characters are preserved
    assert _sanitize_value("test-value_123.txt") == "test-value_123.txt"
    assert _sanitize_value("/path/to/file") == "/path/to/file"
    assert _sanitize_value("normal text") == "normal text"
    
    # Test edge cases
    assert _sanitize_value("") == ""
    assert _sanitize_value(None) == ""


def test_validate_command_template_safe():
    """Test that safe command templates pass validation."""
    safe_commands = [
        "date",
        "hostname",
        "df -k",
        "systemctl status {{service_name}}",
        "ls -la /var/log",
        "echo 'test message'",
        "cat /proc/cpuinfo",
        "ps aux | grep {{process_name}}"
    ]
    
    for cmd in safe_commands:
        # Should not raise exception
        _validate_command_template(cmd)


def test_validate_command_template_dangerous():
    """Test that dangerous command templates are rejected."""
    dangerous_commands = [
        "rm -rf /",
        "sudo rm -rf /",
        "$(rm -rf /)",
        "`rm -rf /`",
        "date && rm -rf /",
        "date || rm -rf /",
        "date; rm -rf /",
        "cat /etc/passwd > /dev/null",
        "echo test > /dev/sda",
        "su - root",
        "sudo su -"
    ]
    
    for cmd in dangerous_commands:
        with pytest.raises(ValueError, match="Dangerous command pattern detected"):
            _validate_command_template(cmd)


def test_validate_and_sanitize_context():
    """Test context validation and sanitization."""
    context = {
        "safe_var": "safe_value",
        "dangerous_var": "dangerous;rm -rf /",
        "null_var": None,
        "int_var": 123
    }
    
    sanitized = _validate_and_sanitize_context(context)
    
    assert sanitized["safe_var"] == "safe_value"
    assert sanitized["dangerous_var"] == "dangerous rm -rf "
    assert sanitized["null_var"] == ""
    assert sanitized["int_var"] == "123"


def test_render_command_safe():
    """Test safe command rendering."""
    module = DynamicModule(
        id=1,
        name="test_module",
        system_name="TestSystem",
        enabled=True,
        timezone="UTC",
        daily_time="08:00",
        recipient_emails=[]
    )
    
    host = DynamicModuleHost(
        id=1,
        name="test_host",
        ip="192.168.1.100",
        variables={"service_name": "nginx", "path": "/var/log"}
    )
    
    # Safe command should render correctly
    cmd_template = "systemctl status {{service_name}} && ls {{path}}"
    rendered = _render_command(cmd_template, module, host)
    
    expected = "systemctl status nginx && ls /var/log"
    assert rendered == expected


def test_render_command_sanitization():
    """Test that dangerous values are sanitized during rendering."""
    module = DynamicModule(
        id=1,
        name="test_module",
        system_name="TestSystem",
        enabled=True,
        timezone="UTC",
        daily_time="08:00",
        recipient_emails=[]
    )
    
    host = DynamicModuleHost(
        id=1,
        name="test_host",
        ip="192.168.1.100",
        variables={"service_name": "nginx;rm -rf /", "path": "/var/log`whoami`"}
    )
    
    cmd_template = "systemctl status {{service_name}} && ls {{path}}"
    rendered = _render_command(cmd_template, module, host)
    
    # Dangerous characters should be removed
    assert "rm -rf" not in rendered
    assert "whoami" not in rendered
    assert "nginx" in rendered
    assert "/var/log" in rendered


def test_render_command_dangerous_template():
    """Test that dangerous command templates are rejected."""
    module = DynamicModule(
        id=1,
        name="test_module",
        system_name="TestSystem",
        enabled=True,
        timezone="UTC",
        daily_time="08:00",
        recipient_emails=[]
    )
    
    host = DynamicModuleHost(
        id=1,
        name="test_host",
        ip="192.168.1.100",
        variables={}
    )
    
    dangerous_templates = [
        "rm -rf /",
        "sudo rm -rf /",
        "$(whoami)",
        "`whoami`",
        "date && rm -rf /"
    ]
    
    for template in dangerous_templates:
        with pytest.raises(ValueError, match="Dangerous command pattern detected"):
            _render_command(template, module, host)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
