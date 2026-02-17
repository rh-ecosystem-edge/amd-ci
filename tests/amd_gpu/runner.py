"""AMD GPU test runner utilities.

Provides functions for running the AMD GPU verification test suite,
both locally and against remote clusters via SSH tunnel.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import yaml

from shared.ssh import SSH_BASE_OPTS


def run_gpu_tests(kubeconfig_path: str | Path) -> int:
    """Run AMD GPU verification tests.

    Returns the pytest exit code (0 = all tests passed).
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    test_dir = repo_root / "tests" / "amd_gpu"

    if not test_dir.is_dir():
        print(f"  Warning: test directory not found at {test_dir}, skipping tests.")
        return 0

    print("\n" + "=" * 60)
    print("Running AMD GPU Verification Tests")
    print("=" * 60)

    env = {
        **os.environ,
        "KUBECONFIG": str(Path(kubeconfig_path).resolve()),
        "PYTHONPATH": str(repo_root),
    }

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_dir), "-v"],
        env=env,
        cwd=str(repo_root),
    )

    if result.returncode == 0:
        print("\n" + "=" * 60)
        print("AMD GPU Verification Tests: ALL PASSED")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print(f"AMD GPU Verification Tests: FAILED (exit code {result.returncode})")
        print("=" * 60)

    return result.returncode


def run_gpu_tests_remote(
    remote_host: str,
    remote_user: str,
    kubeconfig_path: Path,
    ssh_key_path: str | None = None,
) -> int:
    """Run GPU tests against a remote cluster via an SSH tunnel.

    Sets up an SSH port-forward from a local port to the cluster API server,
    creates a temporary kubeconfig that points at the tunnel endpoint, runs
    the tests, and tears everything down.

    Returns the pytest exit code (0 = all tests passed).
    """
    with open(kubeconfig_path) as f:
        kc = yaml.safe_load(f)

    server_url = kc["clusters"][0]["cluster"]["server"]
    parsed = urlparse(server_url)
    api_host = parsed.hostname
    api_port = parsed.port or 6443

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        local_port = s.getsockname()[1]

    ssh_opts = SSH_BASE_OPTS
    if ssh_key_path:
        ssh_opts += f" -i {ssh_key_path}"

    tunnel_cmd = (
        f"ssh {ssh_opts} -L 127.0.0.1:{local_port}:{api_host}:{api_port} "
        f"-N {remote_user}@{remote_host}"
    )
    print(f"  Opening SSH tunnel (local :{local_port} -> {api_host}:{api_port} via {remote_host})...")
    tunnel = subprocess.Popen(tunnel_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    tunnel_ready = False
    for _ in range(30):
        if tunnel.poll() is not None:
            stderr = tunnel.stderr.read().decode() if tunnel.stderr else ""
            raise RuntimeError(f"SSH tunnel failed to start: {stderr}")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=1):
                tunnel_ready = True
                break
        except OSError:
            time.sleep(1)
    if not tunnel_ready:
        tunnel.terminate()
        raise RuntimeError("SSH tunnel started but port is not reachable after 30s")

    kc["clusters"][0]["cluster"]["server"] = f"https://127.0.0.1:{local_port}"
    kc["clusters"][0]["cluster"].pop("certificate-authority-data", None)
    kc["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = True

    tmp_kc = tempfile.NamedTemporaryFile(
        mode="w", suffix=".kubeconfig", prefix="gpu-test-", delete=False
    )
    yaml.dump(kc, tmp_kc)
    tmp_kc.close()

    try:
        return run_gpu_tests(tmp_kc.name)
    finally:
        Path(tmp_kc.name).unlink(missing_ok=True)
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel.kill()
        print("  SSH tunnel closed.")
