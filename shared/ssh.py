"""
SSH utilities shared across cluster-provision, operators, and tests.

Provides SSH/SCP command wrappers and key management for connecting to
remote hosts in CI/CD environments.
"""

from __future__ import annotations

import shlex
import stat
import subprocess
from pathlib import Path
from typing import Optional


SSH_CONTROL_PATH = "/tmp/ssh-mux-%r@%h:%p"

# Base SSH options for non-interactive CI/CD use
SSH_BASE_OPTS = (
    "-o StrictHostKeyChecking=no "
    "-o UserKnownHostsFile=/dev/null "
    "-o LogLevel=ERROR "
    "-o ConnectTimeout=30 "
    "-o ServerAliveInterval=10 "
    "-o ServerAliveCountMax=3 "
    "-o BatchMode=yes "
    f"-o ControlPath={SSH_CONTROL_PATH} "
    "-o ControlMaster=auto "
    "-o ControlPersist=600"
)

# Module-level SSH key path (set via set_ssh_key_path)
ssh_key_path: Optional[str] = None


def set_ssh_key_path(key_path: Optional[str]) -> None:
    """
    Set the SSH key path to use for all SSH connections.
    Automatically fixes permissions to 600 if needed.
    """
    global ssh_key_path

    if key_path:
        key_file = Path(key_path)

        if not key_file.exists():
            raise FileNotFoundError(f"SSH key file not found: {key_path}")

        current_mode = key_file.stat().st_mode
        if current_mode & 0o777 != 0o600:
            print(f"Fixing SSH key permissions: {key_path} (chmod 600)")
            key_file.chmod(0o600)

    ssh_key_path = key_path


def get_ssh_opts() -> str:
    """Get SSH options, including identity file if configured."""
    if ssh_key_path:
        return f"{SSH_BASE_OPTS} -i {ssh_key_path}"
    return SSH_BASE_OPTS


def ssh_cmd(
    host: str,
    user: str,
    command: str,
    check: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Execute a command on the remote host via SSH."""
    ssh_opts = get_ssh_opts()
    full_cmd = f"ssh {ssh_opts} {user}@{host} {shlex.quote(command)}"
    try:
        return subprocess.run(
            full_cmd,
            shell=True,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  SSH command timed out after {timeout}s: {command[:80]}")
        return subprocess.CompletedProcess(
            args=full_cmd, returncode=1,
            stdout="", stderr=f"SSH command timed out after {timeout}s",
        )


def scp_cmd(
    src: str,
    dest: str,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Copy a file via SCP."""
    ssh_opts = get_ssh_opts()
    full_cmd = f"scp {ssh_opts} {src} {dest}"
    return subprocess.run(
        full_cmd,
        shell=True,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def close_ssh_multiplexing(host: str, user: str) -> None:
    """Close the SSH ControlMaster connection for a given host."""
    ssh_opts = get_ssh_opts()
    cmd = f"ssh {ssh_opts} -O exit {user}@{host}"
    subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
