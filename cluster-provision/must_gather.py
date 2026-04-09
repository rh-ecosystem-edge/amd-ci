"""
Must-gather support for local and remote clusters.

Local: runs scripts/must-gather.sh with the given KUBECONFIG.
Remote: SCP the script to the remote host and execute it there
        (where /root/kubeconfig already exists from deploy), then
        stream the results back via tar-over-SSH.

The remote path uses SCP + SSH (rather than RemoteOcRunner) because
must-gather.sh runs many heavy oc commands (oc adm inspect, log
collection) that benefit from running locally on the cluster host.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from shared.ssh import ssh_cmd, scp_cmd, get_ssh_opts, close_ssh_multiplexing
from shared.oc_runner import REMOTE_KUBECONFIG

MUST_GATHER_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "must-gather.sh"

MUST_GATHER_TIMEOUT = 600


def run_must_gather(kubeconfig: str, artifact_dir: str) -> int:
    """Run must-gather locally with the given kubeconfig."""
    env = {**os.environ, "KUBECONFIG": kubeconfig, "ARTIFACT_DIR": artifact_dir}
    result = subprocess.run(
        [str(MUST_GATHER_SCRIPT)], env=env, text=True, timeout=MUST_GATHER_TIMEOUT,
    )
    return result.returncode


def run_must_gather_remote(host: str, user: str, artifact_dir: str) -> int:
    """
    Run must-gather on a remote host:
    1. Create a unique temp dir on the remote via mktemp
    2. SCP the must-gather script into it
    3. Execute it remotely with KUBECONFIG pointing to the remote kubeconfig
    4. Stream the results back via tar-over-SSH
    5. Always clean up the remote temp dir (even on failure)
    """
    remote_workdir = None
    try:
        mktemp_result = ssh_cmd(
            host, user, "mktemp -d /tmp/must-gather-XXXXXXXX", check=False, timeout=30,
        )
        if mktemp_result.returncode != 0:
            print(f"[must-gather] Failed to create remote temp dir: {mktemp_result.stderr}")
            return 1
        remote_workdir = mktemp_result.stdout.strip()

        remote_script = f"{remote_workdir}/must-gather.sh"
        remote_artifact_dir = f"{remote_workdir}/output"

        print(f"[must-gather] Copying script to {user}@{host}:{remote_script}")
        scp_cmd(str(MUST_GATHER_SCRIPT), f"{user}@{host}:{remote_script}")

        remote_cmd = (
            f"chmod +x {remote_script} && "
            f"KUBECONFIG={REMOTE_KUBECONFIG} "
            f"ARTIFACT_DIR={remote_artifact_dir} "
            f"{remote_script}"
        )

        print(f"[must-gather] Running must-gather on {host}")
        result = ssh_cmd(host, user, remote_cmd, check=False, timeout=MUST_GATHER_TIMEOUT)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)

        if result.returncode != 0:
            print(f"[must-gather] Remote script failed with exit code {result.returncode}")
            return result.returncode

        local_artifact = Path(artifact_dir)
        local_artifact.mkdir(parents=True, exist_ok=True)

        print(f"[must-gather] Copying results from {host} to {artifact_dir}")
        ssh_opts = get_ssh_opts()
        tar_pipeline = [
            "ssh", *ssh_opts.split(), f"{user}@{host}",
            f"tar -cf - -C {remote_artifact_dir} .",
        ]
        tar_extract = ["tar", "-xf", "-", "-C", str(local_artifact)]
        ssh_proc = subprocess.Popen(tar_pipeline, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        extract_proc = subprocess.Popen(tar_extract, stdin=ssh_proc.stdout, stderr=subprocess.PIPE)
        ssh_proc.stdout.close()

        _, extract_err = extract_proc.communicate(timeout=300)
        ssh_proc.wait(timeout=10)

        if ssh_proc.returncode != 0 or extract_proc.returncode != 0:
            ssh_err = ssh_proc.stderr.read().decode() if ssh_proc.stderr else ""
            print(f"[must-gather] Warning: failed to copy results back: {ssh_err}{extract_err.decode()}")
            return 1

        print(f"[must-gather] Results saved to {artifact_dir}")
        return 0

    finally:
        if remote_workdir:
            ssh_cmd(host, user, f"rm -rf {remote_workdir}", check=False, timeout=30)
        close_ssh_multiplexing(host, user)
