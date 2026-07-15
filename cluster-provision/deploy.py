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
from shared.oc_runner import OcRunner, LocalOcRunner, RemoteOcRunner, REMOTE_KUBECONFIG
from vm import shutdown_vms, start_vms, fix_container_storage, attach_pci_devices


def push_ssh_key_to_remote(host: str, user: str) -> None:
    """Copy the CI runner's SSH private key to the remote host.

    kcli injects the CI runner's public key into VMs, but the remote host
    needs the matching private key to SSH into its own VMs.
    """
    from shared.ssh import ssh_key_path, scp_cmd, ssh_cmd

    local_key = Path(ssh_key_path) if ssh_key_path else Path.home() / ".ssh" / "id_rsa"
    if not local_key.exists():
        print("  No SSH key to push — skipping.")
        return

    print("Copying SSH key to remote host for VM access")
    scp_cmd(str(local_key), f"{user}@{host}:/root/.ssh/id_rsa")
    ssh_cmd(host, user, "chmod 600 /root/.ssh/id_rsa", check=False)
    ssh_cmd(
        host, user,
        "ssh-keygen -y -f /root/.ssh/id_rsa > /root/.ssh/id_rsa.pub 2>/dev/null",
        check=False,
    )


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
    remote_host: Optional[str],
    remote_user: str,
    wait_timeout: int,
    ssh_key: Optional[str],
    pci_devices: Optional[List[str]],
) -> str:
    """
    Main deployment flow, driven by the kcli parameters.

    Deploys the cluster and waits for it to be ready. Does NOT install
    operators or run tests — use the separate 'operators' and 'test-gpu'
    commands for those.

    Args:
        params: Parameters dictionary (from config.get_kcli_params)
        remote_host: Remote libvirt host (None for local deployment)
        remote_user: SSH user for remote host
        wait_timeout: Timeout in seconds for cluster ready (remote only)
        ssh_key: Path to SSH private key file (optional)
        pci_devices: List of PCI device addresses for passthrough (e.g., ["0000:b3:00.0"])

    Returns:
        The actual deployed OCP version string (e.g. "4.22.1").
    """
    ensure_kcli_installed()
    
    cluster_name = params["cluster"]
    api_ip = params["api_ip"]
    domain = params["domain"]
    ctlplanes = int(params["ctlplanes"])
    workers = int(params["workers"])

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
        return deploy_remote(
            params=params,
            cluster_name=cluster_name,
            api_ip=api_ip,
            domain=domain,
            ctlplanes=ctlplanes,
            workers=workers,
            remote_host=remote_host,
            remote_user=remote_user,
            wait_timeout=wait_timeout,
            ssh_key=ssh_key,
            pci_devices=pci_devices,
        )
    else:
        return deploy_local(
            params=params,
            ctlplanes=ctlplanes,
            workers=workers,
        )


def get_deployed_cluster_version(oc: OcRunner) -> str:
    """Query the actual OCP version running on the cluster.

    Works with both LocalOcRunner and RemoteOcRunner.
    Returns the version string (e.g. "4.22.1") or empty string on failure.
    """
    result = oc.oc("get", "clusterversion", "version", "--no-headers", timeout=30)
    if result.returncode != 0 or not result.stdout:
        print(f"  Warning: failed to query cluster version (rc={result.returncode})")
        return ""
    # Output format: NAME VERSION AVAILABLE PROGRESSING SINCE STATUS
    parts = result.stdout.strip().split()
    if len(parts) >= 2:
        return parts[1]
    return ""


def deploy_local(
    params: Dict[str, Any],
    ctlplanes: int,
    workers: int,
) -> str:
    """Deploy OpenShift cluster locally using kcli.

    Returns the actual deployed OCP version (e.g. "4.22.1").
    """
    ensure_kcli_config()

    topology = get_cluster_topology_description(ctlplanes, workers)

    # Build kcli command with all parameters via -P flags
    kcli_cmd = ["kcli", "create", "cluster", "openshift"]
    kcli_cmd.extend(build_kcli_params(params))
    
    print(f"\nStarting OpenShift deployment [{topology}] with kcli...")
    print(f"  kcli command: {' '.join(kcli_cmd)}")
    run(kcli_cmd, check=True)

    cluster_name = params["cluster"]
    kubeconfig = Path.home() / ".kcli" / "clusters" / cluster_name / "auth" / "kubeconfig"
    oc = LocalOcRunner(str(kubeconfig))
    actual_version = get_deployed_cluster_version(oc)
    print(f"\nOpenShift deployment [{topology}] command has completed. (version: {actual_version})")
    return actual_version


def deploy_remote(
    params: Dict[str, Any],
    cluster_name: str,
    api_ip: str,
    domain: str,
    ctlplanes: int,
    workers: int,
    remote_host: str,
    remote_user: str,
    wait_timeout: int,
    ssh_key: Optional[str] = None,
    pci_devices: Optional[List[str]] = None,
) -> str:
    """Deploy OpenShift cluster on a remote libvirt host.

    Returns the actual deployed OCP version (e.g. "4.22.1").
    """
    from remote import (
        setup_remote_libvirt,
        ensure_host_pci_passthrough,
        configure_kcli_remote_client,
        setup_remote_cluster_access,
        wait_for_cluster_ready,
        get_cluster_status,
        print_access_instructions,
        set_ssh_key_path,
    )
    
    topology = get_cluster_topology_description(ctlplanes, workers)
    
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
    if ssh_key:
        print(f"SSH Key: {ssh_key}")
    print(f"{'='*60}\n")

    # Step 1: Setup remote host (idempotent)
    print("Step 1: Setting up remote host...")
    setup_remote_libvirt(remote_host, remote_user)

    # Step 1b: Ensure PCI passthrough is enabled on the host (may reboot)
    if pci_devices:
        print("\nStep 1b: Ensuring PCI passthrough is enabled on host...")
        ensure_host_pci_passthrough(remote_host, remote_user, pci_devices)

    # Step 2: Configure kcli client
    print("\nStep 2: Configuring kcli client...")
    kcli_client = configure_kcli_remote_client(remote_host, remote_user)

    # Step 3: Clean up any existing cluster
    print(f"\nStep 3: Cleaning up any existing cluster '{cluster_name}'...")
    run(["kcli", "-C", kcli_client, "delete", "cluster", cluster_name, "--yes"], check=False)
    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        shutil.rmtree(clusters_dir)
    
    # Step 4: Deploy the cluster
    print(f"\nStep 4: Deploying OpenShift cluster [{topology}]...")
    print("Starting kcli deployment (monitoring will be done via remote host)...")
    
    kcli_cmd = ["kcli", "-C", kcli_client, "create", "cluster", "openshift"]
    kcli_cmd.extend(build_kcli_params(params))
    
    print(f"  kcli command: {' '.join(kcli_cmd)}")
    print("\n  Starting kcli in background...")
    
    kcli_process = subprocess.Popen(
        kcli_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    
    expected_vms = ctlplanes + workers + 1
    min_vms_to_proceed = 2

    # Step 5: Wait for VMs to be deployed
    print("\nStep 5: Waiting for VMs to be deployed...")
    vm_wait_timeout = 600
    vm_wait_start = time.time()
    while True:
        if kcli_process.poll() is not None:
            stdout, _ = kcli_process.communicate()
            if kcli_process.returncode != 0:
                print(f"\n✗ kcli process exited with code {kcli_process.returncode}")
                print("kcli output:")
                print(stdout)
                raise DeployError(f"kcli deployment failed with exit code {kcli_process.returncode}")
        
        result = run(["kcli", "-C", kcli_client, "list", "vm"], check=False, capture_output=True)
        vm_count = result.stdout.count(f"{cluster_name}-")
        
        if vm_count >= min_vms_to_proceed:
            print(f"  VMs deployed: {vm_count} VMs found (expecting {expected_vms} total)")
            break
        
        elapsed = int(time.time() - vm_wait_start)
        if elapsed >= vm_wait_timeout:
            try:
                kcli_process.terminate()
                stdout, _ = kcli_process.communicate(timeout=5)
                print("\nkcli output:")
                print(stdout)
            except Exception:
                pass
            raise DeployError("Timeout waiting for VMs to be deployed (10 minutes)")
        
        if elapsed % 30 == 0 or vm_count > 0:
            print(f"  Waiting for VMs... ({elapsed}s elapsed, found {vm_count} VMs, expecting {expected_vms})")
        time.sleep(10)
    
    print("\nVMs on remote host:")
    run(["kcli", "-C", kcli_client, "list", "vm"], check=False)

    push_ssh_key_to_remote(remote_host, remote_user)

    # Step 5b/5c: PCI attachment + container storage fix.
    # Both need the VM shut off. When PCI devices are requested the
    # attach_pci_devices helper already shuts down the VM, so we hook
    # the storage fix into that window via pre_start_hook. Without PCI
    # devices we shut down/start the VMs ourselves.
    if pci_devices:
        print("\nStep 5b: Attaching PCI devices to control plane VM...")
        ctlplane_vm = f"{cluster_name}-ctlplane-0"
        attach_pci_devices(
            remote_host, remote_user, ctlplane_vm, pci_devices,
            pre_start_hook=lambda: fix_container_storage(
                remote_host, remote_user, cluster_name, ctlplanes),
        )
    else:
        shutdown_vms(remote_host, remote_user, cluster_name, ctlplanes)
        fix_container_storage(remote_host, remote_user, cluster_name, ctlplanes)
        start_vms(remote_host, remote_user, cluster_name, ctlplanes)

    # Step 6: Setup remote cluster access
    print("\nStep 6: Setting up remote cluster access...")
    setup_remote_cluster_access(remote_host, remote_user, cluster_name, api_ip, domain)
    
    # Kill kcli process
    print("\nStopping kcli monitoring (we'll monitor via remote host)...")
    kcli_process.terminate()
    try:
        kcli_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kcli_process.kill()
    
    # Step 7: Wait for cluster to be ready
    print(f"\nStep 7: Waiting for cluster to be ready...")
    wait_for_cluster_ready(remote_host, remote_user, api_ip, wait_timeout)
    
    # Final status
    print("\n" + "=" * 60)
    print("CLUSTER STATUS")
    print("=" * 60)
    status = get_cluster_status(remote_host, remote_user)
    print(status)
    
    print_access_instructions(
        host=remote_host,
        user=remote_user,
        cluster_name=cluster_name,
        api_ip=api_ip,
        domain=domain,
        kcli_client=kcli_client,
    )

    oc = RemoteOcRunner(remote_host, remote_user, REMOTE_KUBECONFIG)
    actual_version = get_deployed_cluster_version(oc)
    print(f"\nDeployment completed successfully! (version: {actual_version})")
    return actual_version
