"""
Deploy OpenShift cluster using kcli.
Supports both local and remote libvirt hosts.
Default topology is SNO (Single Node OpenShift): 1 control plane, 0 workers.
"""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import DeployError, run
from config import get_cluster_topology_description
from kcli_preflight import ensure_kcli_installed, ensure_pull_secret_exists, ensure_kcli_config


def build_kcli_params(params: Dict[str, str]) -> List[str]:
    """
    Build kcli -P parameter arguments from params dict.
    
    Returns:
        List of ["-P", "key=value", "-P", "key2=value2", ...]
    """
    args = []
    for key, value in params.items():
        args.extend(["-P", f"{key}={value}"])
    return args


def deploy_cluster(
    params: Dict[str, Any],
    remote_host: Optional[str] = None,
    remote_user: str = "root",
    wait_timeout: int = 3600,
    no_wait: bool = False,
    ssh_key: Optional[str] = None,
    pci_devices: Optional[List[str]] = None,
) -> None:
    """
    Main deployment flow, driven by the kcli parameters.

    Args:
        params: Parameters dictionary (from config.get_kcli_params)
        remote_host: Remote libvirt host (None for local deployment)
        remote_user: SSH user for remote host
        wait_timeout: Timeout in seconds for cluster ready (remote only)
        no_wait: Skip waiting for cluster ready (remote only)
        ssh_key: Path to SSH private key file (optional)
        pci_devices: List of PCI device addresses for passthrough (e.g., ["0000:b3:00.0"])
    """
    ensure_kcli_installed()
    
    cluster_name = params.get("cluster", "ocp")
    api_ip = params.get("api_ip", "192.168.122.253")
    domain = params.get("domain", "example.com")
    ctlplanes = int(params.get("ctlplanes", 1))
    workers = int(params.get("workers", 0))

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
            ctlplanes=ctlplanes,
            workers=workers,
            remote_host=remote_host,
            remote_user=remote_user,
            wait_timeout=wait_timeout,
            no_wait=no_wait,
            ssh_key=ssh_key,
            pci_devices=pci_devices,
        )
    else:
        _deploy_local(
            params=params,
            ctlplanes=ctlplanes,
            workers=workers,
        )


def _deploy_local(params: Dict[str, Any], ctlplanes: int, workers: int) -> None:
    """Deploy OpenShift cluster locally."""
    ensure_kcli_config()

    topology = get_cluster_topology_description(ctlplanes, workers)

    # Build kcli command with all parameters via -P flags
    kcli_cmd = ["kcli", "create", "cluster", "openshift"]
    kcli_cmd.extend(build_kcli_params(params))
    
    print(f"\nStarting OpenShift deployment [{topology}] with kcli...")
    print(f"  kcli command: {' '.join(kcli_cmd)}")
    run(kcli_cmd, check=True)
    print(f"\nOpenShift deployment [{topology}] command has completed.")
    print("Check 'kcli list' and the OpenShift console once the cluster is fully up.")


def _deploy_remote(
    params: Dict[str, Any],
    cluster_name: str,
    api_ip: str,
    domain: str,
    ctlplanes: int,
    workers: int,
    remote_host: str,
    remote_user: str,
    wait_timeout: int,
    no_wait: bool,
    ssh_key: Optional[str] = None,
    pci_devices: Optional[List[str]] = None,
) -> None:
    """Deploy OpenShift cluster on a remote libvirt host."""
    from remote import (
        setup_remote_libvirt,
        configure_kcli_remote_client,
        setup_remote_cluster_access,
        wait_for_cluster_ready,
        get_cluster_status,
        print_access_instructions,
        set_ssh_key_path,
        attach_pci_devices,
    )
    
    topology = get_cluster_topology_description(ctlplanes, workers)
    
    # Configure SSH key if provided
    if ssh_key:
        set_ssh_key_path(ssh_key)
        print(f"Using SSH key: {ssh_key}")
    
    print(f"\n{'='*60}")
    print(f"Remote OpenShift Deployment [{topology}]")
    print(f"{'='*60}")
    print(f"Remote Host: {remote_user}@{remote_host}")
    print(f"Cluster Name: {cluster_name}")
    print(f"Topology: {topology}")
    print(f"API IP: {api_ip}")
    print(f"Domain: {domain}")
    print(f"Wait Timeout: {wait_timeout}s")
    print(f"No Wait: {no_wait}")
    if ssh_key:
        print(f"SSH Key: {ssh_key}")
    print(f"{'='*60}\n")

    # Setup remote host (idempotent)
    print("Step 1: Setting up remote host...")
    setup_remote_libvirt(remote_host, remote_user)

    # Configure kcli client
    print("\nStep 2: Configuring kcli client...")
    kcli_client = configure_kcli_remote_client(remote_host, remote_user)

    # Clean up any existing cluster
    print(f"\nStep 3: Cleaning up any existing cluster '{cluster_name}'...")
    run(["kcli", "-C", kcli_client, "delete", "cluster", cluster_name, "--yes"], check=False)
    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        shutil.rmtree(clusters_dir)
    
    # Deploy the cluster (run in background, we'll monitor ourselves)
    print(f"\nStep 4: Deploying OpenShift cluster [{topology}]...")
    print("Starting kcli deployment (monitoring will be done via remote host)...")
    
    # Build kcli command with all parameters via -P flags
    kcli_cmd = ["kcli", "-C", kcli_client, "create", "cluster", "openshift"]
    kcli_cmd.extend(build_kcli_params(params))
    
    print(f"  kcli command: {' '.join(kcli_cmd)}")
    print("\n  Starting kcli in background...")
    
    # Start kcli in background
    kcli_process = subprocess.Popen(
        kcli_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    
    # Calculate expected VM count: ctlplanes + workers + 1 bootstrap
    # kcli creates a bootstrap VM during installation
    expected_vms = ctlplanes + workers + 1
    min_vms_to_proceed = 2  # At least bootstrap + 1 node
    
    # Wait for VMs to be deployed
    print("\nStep 5: Waiting for VMs to be deployed...")
    vm_wait_timeout = 600
    vm_wait_start = time.time()
    while True:
        # Check if kcli process died unexpectedly
        if kcli_process.poll() is not None:
            # Process finished, get its output
            stdout, _ = kcli_process.communicate()
            if kcli_process.returncode != 0:
                print(f"\n✗ kcli process exited with code {kcli_process.returncode}")
                print("kcli output:")
                print(stdout)
                raise DeployError(f"kcli deployment failed with exit code {kcli_process.returncode}")
            # If it succeeded (exit 0), that's fine, continue waiting for VMs
        
        result = run(["kcli", "-C", kcli_client, "list", "vm"], check=False, capture_output=True)
        vm_count = result.stdout.count(f"{cluster_name}-")
        
        if vm_count >= min_vms_to_proceed:
            print(f"  VMs deployed: {vm_count} VMs found (expecting {expected_vms} total)")
            break
        
        elapsed = int(time.time() - vm_wait_start)
        if elapsed >= vm_wait_timeout:
            # Get kcli output before failing
            try:
                kcli_process.terminate()
                stdout, _ = kcli_process.communicate(timeout=5)
                print("\nkcli output:")
                print(stdout)
            except:
                pass
            raise DeployError("Timeout waiting for VMs to be deployed (10 minutes)")
        
        # Print status every 30 seconds or if we find VMs
        if elapsed % 30 == 0 or vm_count > 0:
            print(f"  Waiting for VMs... ({elapsed}s elapsed, found {vm_count} VMs, expecting {expected_vms})")
        time.sleep(10)
    
    # Show deployed VMs
    print("\nVMs on remote host:")
    run(["kcli", "-C", kcli_client, "list", "vm"], check=False)
    
    # Attach PCI devices if specified
    if pci_devices:
        print("\nStep 5b: Attaching PCI devices to control plane VM...")
        ctlplane_vm = f"{cluster_name}-ctlplane-0"
        attach_pci_devices(remote_host, remote_user, ctlplane_vm, pci_devices)
    
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
