"""
Delete OpenShift cluster using kcli.
Supports both local and remote libvirt hosts.
"""

import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from common import run
from kcli_preflight import ensure_kcli_installed


def delete_cluster(
    params: Dict[str, Any],
    dry_run: bool = False,
    remote_host: Optional[str] = None,
    remote_user: str = "root",
    ssh_key: Optional[str] = None,
) -> None:
    """
    Delete the OpenShift cluster.
    
    Args:
        params: Parameters dictionary (must contain 'cluster' key)
        dry_run: If True, don't actually run kcli commands
        remote_host: Remote libvirt host (None for local deletion)
        remote_user: SSH user for remote host
        ssh_key: Path to SSH private key file (optional)
    """
    ensure_kcli_installed()
    
    cluster_name = params.get("cluster", "ocp")
    
    print(f"Preparing to delete cluster: {cluster_name}")
    
    if remote_host:
        _delete_remote(cluster_name, remote_host, remote_user, dry_run, ssh_key)
    else:
        _delete_local(cluster_name, dry_run)


def _delete_local(cluster_name: str, dry_run: bool) -> None:
    """Delete OpenShift cluster locally."""
    if dry_run:
        print(f"Dry run: would execute 'kcli delete cluster {cluster_name} --yes'")
        return
        
    print(f"Deleting cluster {cluster_name}...")
    run(["kcli", "delete", "cluster", cluster_name, "--yes"], check=True)
    
    # Clean up local artifacts
    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        print(f"Removing cluster artifacts directory: {clusters_dir}")
        shutil.rmtree(clusters_dir)
    
    print(f"Cluster {cluster_name} deleted.")


def _delete_remote(
    cluster_name: str,
    remote_host: str,
    remote_user: str,
    dry_run: bool,
    ssh_key: Optional[str] = None,
) -> None:
    """Delete OpenShift cluster on a remote libvirt host."""
    from remote import get_kcli_client_name, configure_kcli_remote_client, check_ssh_connectivity, set_ssh_key_path
    
    # Configure SSH key if provided
    if ssh_key:
        set_ssh_key_path(ssh_key)
        print(f"Using SSH key: {ssh_key}")
    
    print(f"\nDeleting remote cluster: {cluster_name}")
    print(f"Remote host: {remote_user}@{remote_host}")
    
    # Check SSH connectivity first
    if not check_ssh_connectivity(remote_host, remote_user):
        print(f"WARNING: Cannot connect to {remote_user}@{remote_host} via SSH")
        print("Attempting to delete using existing kcli configuration...")
    
    # Get or create kcli client
    kcli_client = get_kcli_client_name(remote_host)
    
    # Check if client exists, if not configure it
    result = run(["kcli", "-C", kcli_client, "list", "vm"], check=False, capture_output=True)
    if result.returncode != 0:
        print(f"Configuring kcli client '{kcli_client}'...")
        kcli_client = configure_kcli_remote_client(remote_host, remote_user)
    
    if dry_run:
        print(f"Dry run: would execute 'kcli -C {kcli_client} delete cluster {cluster_name} --yes'")
        return
    
    print(f"Deleting cluster {cluster_name} from remote host...")
    run(
        ["kcli", "-C", kcli_client, "delete", "cluster", cluster_name, "--yes"],
        check=False,
    )
    
    # Clean up local artifacts
    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        print(f"Removing cluster artifacts directory: {clusters_dir}")
        shutil.rmtree(clusters_dir)
    
    print(f"Cluster {cluster_name} deletion complete.")
