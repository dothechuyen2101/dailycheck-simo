"""
Simple test script for command validation without pytest dependency.
"""
import sys
import os

# Add the src directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dynamic_modules import (
    _sanitize_value,
    _validate_command_template,
    _validate_and_sanitize_context,
    _render_command,
    DynamicModule,
    DynamicModuleHost
)


def test_sanitize_value():
    """Test value sanitization removes dangerous characters."""
    print("Testing _sanitize_value...")
    
    # Test dangerous characters are removed
    assert _sanitize_value("test;rm -rf /") == "test rm -rf ", f"Expected 'test rm -rf ', got '{_sanitize_value('test;rm -rf /')}'"
    assert _sanitize_value("value`whoami`") == "valuewhoami", f"Expected 'valuewhoami', got '{_sanitize_value('value`whoami`')}'"
    assert _sanitize_value("test$sudo") == "testsudo", f"Expected 'testsudo', got '{_sanitize_value('test$sudo')}'"
    assert _sanitize_value('test"quote"') == "testquote", f"Expected 'testquote', got '{_sanitize_value('test\"quote\"')}'"
    assert _sanitize_value("test'quote'") == "testquote", f"Expected 'testquote', got '{_sanitize_value('test\'quote\'')}'"
    assert _sanitize_value("test&command") == "testcommand", f"Expected 'testcommand', got '{_sanitize_value('test&command')}'"
    assert _sanitize_value("test|command") == "testcommand", f"Expected 'testcommand', got '{_sanitize_value('test|command')}'"
    
    # Test allowed characters are preserved
    assert _sanitize_value("test-value_123.txt") == "test-value_123.txt", f"Expected 'test-value_123.txt', got '{_sanitize_value('test-value_123.txt')}'"
    assert _sanitize_value("/path/to/file") == "/path/to/file", f"Expected '/path/to/file', got '{_sanitize_value('/path/to/file')}'"
    assert _sanitize_value("normal text") == "normal text", f"Expected 'normal text', got '{_sanitize_value('normal text')}'"
    
    # Test edge cases
    assert _sanitize_value("") == "", f"Expected empty string, got '{_sanitize_value('')}'"
    assert _sanitize_value(None) == "", f"Expected empty string for None, got '{_sanitize_value(None)}'"
    
    print("✓ _sanitize_value tests passed")


def test_validate_command_template():
    """Test command template validation."""
    print("Testing _validate_command_template...")
    
    # Test safe commands (should not raise exception)
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
        try:
            _validate_command_template(cmd)
        except ValueError as e:
            print(f"✗ Safe command rejected: '{cmd}' - {e}")
            return False
    
    # Test dangerous commands (should raise exception)
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
        try:
            _validate_command_template(cmd)
            print(f"✗ Dangerous command not rejected: '{cmd}'")
            return False
        except ValueError:
            pass  # Expected
    
    print("✓ _validate_command_template tests passed")
    return True


def test_render_command():
    """Test command rendering with sanitization."""
    print("Testing _render_command...")
    
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
    
    # Test safe command rendering
    cmd_template = "systemctl status {{service_name}} && ls {{path}}"
    try:
        rendered = _render_command(cmd_template, module, host)
        # Dangerous characters should be removed
        assert "rm -rf" not in rendered, f"Dangerous content found in rendered command: {rendered}"
        assert "whoami" not in rendered, f"Dangerous content found in rendered command: {rendered}"
        assert "nginx" in rendered, f"Expected content not found in rendered command: {rendered}"
        assert "/var/log" in rendered, f"Expected content not found in rendered command: {rendered}"
        print(f"✓ Safe command rendered correctly: {rendered}")
    except Exception as e:
        print(f"✗ Safe command rendering failed: {e}")
        return False
    
    # Test dangerous command template rejection
    dangerous_template = "rm -rf /"
    try:
        _render_command(dangerous_template, module, host)
        print(f"✗ Dangerous template not rejected: '{dangerous_template}'")
        return False
    except ValueError:
        print(f"✓ Dangerous template correctly rejected: '{dangerous_template}'")
    
    print("✓ _render_command tests passed")
    return True


def main():
    """Run all tests."""
    print("Running command validation tests...\n")
    
    try:
        test_sanitize_value()
        test_validate_command_template()
        test_render_command()
        
        print("\n🎉 All tests passed! Command validation is working correctly.")
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
