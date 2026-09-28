"""
SSH Manager – Quản lý kết nối SSH pool với retry logic.
Sử dụng Paramiko để thực thi lệnh trên máy chủ từ xa.
"""
import logging
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

import paramiko

from src.config_manager import get

logger = logging.getLogger("dailycheck.ssh")


@dataclass
class SSHConfig:
    """Cấu hình kết nối SSH cho một host."""
    host: str
    port: int = 22
    username: str = "oracle"
    key_path: Optional[str] = None
    password: Optional[str] = None
    timeout: int = 30


@dataclass
class CommandResult:
    """Kết quả thực thi lệnh SSH."""
    stdout: str
    stderr: str
    exit_code: int
    success: bool
    command: str = ""


class SSHClient:
    """Wrapper quanh paramiko.SSHClient với retry và timeout."""

    def __init__(self, config: SSHConfig):
        self.config = config
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self, max_retries: int = 2) -> bool:
        """Kết nối SSH. Trả về True nếu thành công."""
        for attempt in range(1, max_retries + 1):
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                connect_kwargs: dict = {
                    "hostname": self.config.host,
                    "port": self.config.port,
                    "username": self.config.username,
                    "timeout": self.config.timeout,
                    "banner_timeout": self.config.timeout,
                    "auth_timeout": self.config.timeout,
                }

                if self.config.key_path:
                    key_path = self.config.key_path.replace("~", os.path.expanduser("~"))
                    try:
                        connect_kwargs["key_filename"] = key_path
                    except Exception:
                        pass

                if self.config.password:
                    connect_kwargs["password"] = self.config.password
                    connect_kwargs["look_for_keys"] = False if not self.config.key_path else True

                client.connect(**connect_kwargs)
                self._client = client
                logger.debug(f"SSH connected: {self.config.username}@{self.config.host}")
                return True
            except (paramiko.AuthenticationException, paramiko.SSHException, socket.error, OSError) as e:
                logger.warning(f"SSH connect attempt {attempt}/{max_retries} failed to {self.config.host}: {e}")
                if attempt < max_retries:
                    time.sleep(2)
        return False

    def run(self, command: str, timeout: int | None = None, input: str | None = None) -> CommandResult:
        """Thực thi lệnh shell. `input` (nếu có) được ghi vào stdin của lệnh, vd. SQL cho sqlplus."""
        if timeout is None:
            timeout = get("general.command_timeout", 60)
        if self._client is None:
            return CommandResult("", "Not connected", -1, False, command)
        try:
            stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
            if input is not None:
                stdin.write(input)
                stdin.flush()
            stdin.channel.shutdown_write()
            # Đọc output trước rồi mới lấy exit status để tránh treo khi output lớn
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            exit_code = stdout.channel.recv_exit_status()
            return CommandResult(out, err, exit_code, exit_code == 0, command)
        except Exception as e:
            logger.error(f"Command failed on {self.config.host}: {command!r} → {e}")
            return CommandResult("", str(e), -1, False, command)

    def run_sudo(self, command: str, timeout: int | None = None) -> CommandResult:
        """Thực thi lệnh với sudo (yêu cầu NOPASSWD sudoers)."""
        return self.run(f"sudo {command}", timeout=timeout)

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def make_ssh_config(host_cfg: dict) -> SSHConfig:
    """Tạo SSHConfig từ dict host trong config.yaml."""
    ssh = host_cfg.get("ssh", {})
    defaults = get("ssh_defaults", {})
    return SSHConfig(
        host=host_cfg["ip"],
        port=ssh.get("port", defaults.get("port", 22)),
        username=ssh.get("username", defaults.get("username", "oracle")),
        key_path=ssh.get("key_path", defaults.get("key_path")),
        password=ssh.get("password", defaults.get("password")) or None,
        timeout=get("general.ssh_timeout", 30),
    )


@contextmanager
def ssh_connect(host_cfg: dict):
    """Context manager tiện lợi để kết nối SSH."""
    cfg = make_ssh_config(host_cfg)
    client = SSHClient(cfg)
    connected = client.connect()
    if not connected:
        raise ConnectionError(f"Cannot SSH to {cfg.host}")
    try:
        yield client
    finally:
        client.close()
