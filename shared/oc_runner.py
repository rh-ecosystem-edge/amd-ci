"""
Abstraction for running oc/kubectl against the cluster (local or remote).

LocalOcRunner runs oc locally with KUBECONFIG.
RemoteOcRunner runs oc on a remote host via SSH (scp + ssh).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from shared.ssh import ssh_cmd, scp_cmd, close_ssh_multiplexing

REMOTE_KUBECONFIG = "/root/kubeconfig"


class OcRunner:
    """
    Runs oc commands against the cluster.
    Implementations: LocalOcRunner (local) and RemoteOcRunner (via SSH).
    """

    def oc(
        self,
        *args: str,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        """Run oc with given args. Optional stdin for apply -f -."""
        raise NotImplementedError

    def apply_yaml(self, yaml_content: str, timeout: int = 120) -> None:
        """Apply YAML (creates/updates resources). Raises RuntimeError on failure."""
        raise NotImplementedError


class LocalOcRunner(OcRunner):
    """Run oc locally with KUBECONFIG."""

    def __init__(self, kubeconfig_path: str | Path) -> None:
        self.kubeconfig = Path(kubeconfig_path).expanduser().resolve()
        if not self.kubeconfig.exists():
            raise RuntimeError(f"Kubeconfig not found: {self.kubeconfig}")

    def oc(
        self,
        *args: str,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        env = {**os.environ, "KUBECONFIG": str(self.kubeconfig)}
        return subprocess.run(
            ["oc"] + list(args),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
        )

    def apply_yaml(self, yaml_content: str, timeout: int = 120) -> None:
        r = self.oc("apply", "-f", "-", timeout=timeout, stdin=yaml_content)
        if r.returncode != 0:
            raise RuntimeError(
                f"oc apply failed: {r.stderr or r.stdout or 'unknown error'}"
            )


class RemoteOcRunner(OcRunner):
    """Run oc on remote host via SSH. Used for operators, tests, and cleanup on remote clusters."""

    def __init__(
        self,
        host: str,
        user: str,
        remote_kubeconfig: str,
    ) -> None:
        self.host = host
        self.user = user
        self.remote_kubeconfig = remote_kubeconfig

    def shell_quote(self, s: str) -> str:
        return "'" + s.replace("'", "'\"'\"'") + "'"

    def oc(
        self,
        *args: str,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        oc_cmd = " ".join(self.shell_quote(a) for a in ("oc",) + args)
        full_cmd = f"KUBECONFIG={self.remote_kubeconfig} {oc_cmd}"
        result = ssh_cmd(
            self.host,
            self.user,
            full_cmd,
            check=False,
            timeout=timeout or 300,
        )
        return subprocess.CompletedProcess(
            args=["oc"] + list(args),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def apply_yaml(self, yaml_content: str, timeout: int = 120) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            local_path = f.name
        remote_path = f"/tmp/apply-{uuid.uuid4().hex}.yaml"
        try:
            scp_cmd(local_path, f"{self.user}@{self.host}:{remote_path}")
            r = self.oc("apply", "-f", remote_path, timeout=timeout)
            if r.returncode != 0:
                raise RuntimeError(
                    f"oc apply failed: {r.stderr or r.stdout or 'unknown error'}"
                )
        finally:
            Path(local_path).unlink(missing_ok=True)
            ssh_cmd(self.host, self.user, f"rm -f {remote_path}", check=False)

    def close(self) -> None:
        """Close the SSH multiplexed connection."""
        close_ssh_multiplexing(self.host, self.user)
