"""
Deploy Single Node OpenShift (SNO) cluster using kcli.
Supports both local and remote libvirt hosts.
"""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

from common import DeployError, run
from kcli_preflight import ensure_kcli_installed, ensure_pull_secret_exists, ensure_kcli_config


def build_kcli_params(params: Dict[str, str]) -> list[str]:
    """
    Build kcli -P parameter arguments from params dict.
    
    Returns:
        List of ["-P", "key=value", "-P", "key2=value2", ...]
    """
    args = []
    for key, value in params.items():
        args.extend(["-P", f"{key}={value}"])
    return args


def deploy_sno(
    params: Dict[str, str],
    dry_run: bool = False,
    remote_host: Optional[str] = None,
    remote_user: str = "root",
    wait_timeout: int = 3600,
    no_wait: bool = False,
) -> None:
    """
    Main deployment flow, driven by the kcli parameters.
    
    Args:
        params: Parameters dictionary (from config.get_kcli_params)
        dry_run: If True, don't actually run kcli commands
        remote_host: Remote libvirt host (None for local deployment)
        remote_user: SSH user for remote host
        wait_timeout: Timeout in seconds for cluster ready (remote only)
        no_wait: Skip waiting for cluster ready (remote only)
    """
    ensure_kcli_installed()
    
    cluster_name = params.get("cluster", "sno")
    api_ip = params.get("api_ip", "192.168.122.253")
    domain = params.get("domain", "example.com")

    # Remove existing kcli cluster artifacts directory, if present
    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        print(f"Removing existing kcli cluster artifacts directory: {clusters_dir}")
        shutil.rmtree(clusters_dir)

    pull_secret_path_str = params.get("pull_secret", "")
    if not pull_secret_path_str:
        raise DeployError("Missing 'pull_secret' in parameters.")
    pull_secret_path = Path(pull_secret_path_str)

    ensure_pull_secret_exists(pull_secret_path)

    # Handle remote vs local deployment
    if remote_host:
        _deploy_remote(
            params=params,
            cluster_name=cluster_name,
            api_ip=api_ip,
            domain=domain,
            remote_host=remote_host,
            remote_user=remote_user,
            dry_run=dry_run,
            wait_timeout=wait_timeout,
            no_wait=no_wait,
        )
    else:
        _deploy_local(
            params=params,
            dry_run=dry_run,
        )


def _deploy_local(params: Dict[str, str], dry_run: bool) -> None:
    """Deploy SNO cluster locally."""
    ensure_kcli_config()

    if dry_run:
        print("\nDry run requested; not invoking 'kcli create cluster'.")
        return

    # Build kcli command with all parameters via -P flags
    kcli_cmd = ["kcli", "create", "cluster", "openshift"]
    kcli_cmd.extend(build_kcli_params(params))
    
    print("\nStarting Single Node OpenShift deployment with kcli...")
    print(f"  kcli command: {' '.join(kcli_cmd)}")
    run(kcli_cmd, check=True)
    print("\nSNO deployment command has completed.")
    print("Check 'kcli list' and the OpenShift console once the node is fully up.")


def _deploy_remote(
    params: Dict[str, str],
    cluster_name: str,
    api_ip: str,
    domain: str,
    remote_host: str,
    remote_user: str,
    dry_run: bool,
    wait_timeout: int,
    no_wait: bool,
) -> None:
    """Deploy SNO cluster on a remote libvirt host."""
    from remote import (
        setup_remote_libvirt,
        configure_kcli_remote_client,
        setup_remote_cluster_access,
        wait_for_cluster_ready,
        get_cluster_status,
        print_access_instructions,
    )
    
    print(f"\n{'='*60}")
    print(f"Remote SNO Deployment")
    print(f"{'='*60}")
    print(f"Remote Host: {remote_user}@{remote_host}")
    print(f"Cluster Name: {cluster_name}")
    print(f"API IP: {api_ip}")
    print(f"Domain: {domain}")
    print(f"Wait Timeout: {wait_timeout}s")
    print(f"No Wait: {no_wait}")
    print(f"{'='*60}\n")
    
    # Setup remote host (idempotent)
    print("Step 1: Setting up remote host...")
    setup_remote_libvirt(remote_host, remote_user)
    
    # Configure kcli client
    print("\nStep 2: Configuring kcli client...")
    kcli_client = configure_kcli_remote_client(remote_host, remote_user)

    if dry_run:
        print("\nDry run requested; not invoking 'kcli create cluster'.")
        return

    # Clean up any existing cluster
    print(f"\nStep 3: Cleaning up any existing cluster '{cluster_name}'...")
    run(["kcli", "-C", kcli_client, "delete", "cluster", cluster_name, "--yes"], check=False)
    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        shutil.rmtree(clusters_dir)
    
    # Deploy the cluster (run in background, we'll monitor ourselves)
    print("\nStep 4: Deploying OpenShift SNO cluster...")
    print("Starting kcli deployment (monitoring will be done via remote host)...")
    
    # Build kcli command with all parameters via -P flags
    kcli_cmd = ["kcli", "-C", kcli_client, "create", "cluster", "openshift"]
    kcli_cmd.extend(build_kcli_params(params))
    
    print(f"  kcli command: {' '.join(kcli_cmd)}")
    
    # Start kcli in background
    kcli_process = subprocess.Popen(
        kcli_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    
    # Wait for VMs to be deployed
    print("\nStep 5: Waiting for VMs to be deployed...")
    vm_wait_timeout = 600
    vm_wait_start = time.time()
    while True:
        result = run(["kcli", "-C", kcli_client, "list", "vm"], check=False, capture_output=True)
        vm_count = result.stdout.count(f"{cluster_name}-")
        
        if vm_count >= 2:
            print(f"  VMs deployed: {vm_count} VMs found")
            break
        
        elapsed = int(time.time() - vm_wait_start)
        if elapsed >= vm_wait_timeout:
            kcli_process.terminate()
            raise DeployError("Timeout waiting for VMs to be deployed")
        
        print(f"  Waiting for VMs... ({elapsed}s elapsed, found {vm_count} VMs)")
        time.sleep(10)
    
    # Show deployed VMs
    print("\nVMs on remote host:")
    run(["kcli", "-C", kcli_client, "list", "vm"], check=False)
    
    # Setup remote cluster access
    print("\nStep 6: Setting up remote cluster access...")
    setup_remote_cluster_access(remote_host, remote_user, cluster_name, api_ip, domain)
    
    # Kill kcli process - we'll do our own monitoring
    print("\nStopping kcli monitoring (we'll monitor via remote host)...")
    kcli_process.terminate()
    try:
        kcli_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kcli_process.kill()
    
    # Wait for cluster to be ready
    if no_wait:
        print("\nNO_WAIT=true, skipping cluster readiness check")
    else:
        print(f"\nStep 7: Waiting for cluster to be ready...")
        wait_for_cluster_ready(remote_host, remote_user, api_ip, wait_timeout)
    
    # Final status
    print("\n" + "=" * 60)
    print("CLUSTER STATUS")
    print("=" * 60)
    status = get_cluster_status(remote_host, remote_user)
    print(status)
    
    # Print access instructions
    print_access_instructions(
        host=remote_host,
        user=remote_user,
        cluster_name=cluster_name,
        api_ip=api_ip,
        domain=domain,
        kcli_client=kcli_client,
    )
    
    print("\nDeployment completed successfully!")
