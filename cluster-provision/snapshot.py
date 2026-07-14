"""
VM snapshot management for cluster caching.

Manages virsh snapshots on a remote libvirt host so that an OCP cluster
can be restored quickly instead of re-deployed from scratch.

Snapshot naming convention: ``ocp-<full_version>``  (e.g. ``ocp-4.22.5``).
Kubeconfig is saved alongside the snapshot so the cluster is fully
restorable without re-running kcli.
"""

from __future__ import annotations

from pathlib import Path

from shared.ssh import ssh_cmd, scp_cmd
from vm import vm_state, shutdown_vm

SNAPSHOT_DIR = "/var/lib/libvirt/amd-ci-snapshots"
SNAPSHOT_PREFIX = "ocp-"


def get_snapshot_name(ocp_version: str) -> str:
    """Build the snapshot name for a given OCP version."""
    return f"{SNAPSHOT_PREFIX}{ocp_version}"


def find_snapshot(
    host: str,
    user: str,
    vm_name: str,
    ocp_version: str,
) -> bool:
    """Check if a snapshot exists for the given OCP version."""
    snap_name = get_snapshot_name(ocp_version)
    r = ssh_cmd(
        host, user,
        f"virsh snapshot-list {vm_name} --name 2>/dev/null | grep -qx '{snap_name}'",
        check=False,
    )
    return r.returncode == 0


def create_snapshot(
    host: str,
    user: str,
    vm_name: str,
    ocp_version: str,
    kubeconfig_local_path: str,
) -> str:
    """Create a snapshot of a shut-off VM and save the kubeconfig.

    The VM must be shut off before calling this function (offline
    snapshots are more reliable and portable than live ones).

    Cluster-level eviction (deleting the oldest cached clusters when
    over ``max_cached``) is handled by the caller before deploying.

    Returns the snapshot name.
    """
    snap_name = get_snapshot_name(ocp_version)

    state = vm_state(host, user, vm_name)
    if state != "shut off":
        raise RuntimeError(
            f"VM {vm_name} must be shut off to create a snapshot "
            f"(current state: {state})"
        )

    if find_snapshot(host, user, vm_name, ocp_version):
        print(f"  Snapshot '{snap_name}' already exists — replacing.")
        delete_snapshot(host, user, vm_name, ocp_version)

    print(f"Creating snapshot '{snap_name}' for VM '{vm_name}'...")
    r = ssh_cmd(
        host, user,
        f"virsh snapshot-create-as {vm_name} --name {snap_name} "
        f"--description 'AMD CI cache: OCP {ocp_version}' "
        f"--atomic",
        check=False,
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"Failed to create snapshot '{snap_name}': {r.stderr or r.stdout}"
        )
    print(f"  Snapshot '{snap_name}' created.")

    try:
        ssh_cmd(host, user, f"mkdir -p {SNAPSHOT_DIR}", check=False)
        scp_cmd(
            kubeconfig_local_path,
            f"{user}@{host}:{SNAPSHOT_DIR}/{snap_name}.kubeconfig",
        )
        print(f"  Kubeconfig saved to {SNAPSHOT_DIR}/{snap_name}.kubeconfig")
    except Exception as exc:
        print(f"  Failed to save kubeconfig — rolling back snapshot: {exc}")
        delete_snapshot(host, user, vm_name, ocp_version)
        raise RuntimeError(
            f"Snapshot '{snap_name}' rolled back: kubeconfig save failed"
        ) from exc

    return snap_name


def revert_snapshot(
    host: str,
    user: str,
    vm_name: str,
    ocp_version: str,
    kubeconfig_local_path: str,
) -> None:
    """Revert a VM to a previously saved snapshot and restore the kubeconfig.

    After reverting, the VM will be in shut-off state (since the snapshot
    was taken while shut off).
    """
    snap_name = get_snapshot_name(ocp_version)

    if not find_snapshot(host, user, vm_name, ocp_version):
        raise RuntimeError(
            f"No snapshot '{snap_name}' found for VM '{vm_name}'"
        )

    if vm_state(host, user, vm_name) == "running":
        shutdown_vm(host, user, vm_name)

    print(f"Reverting VM '{vm_name}' to snapshot '{snap_name}'...")
    r = ssh_cmd(
        host, user,
        f"virsh snapshot-revert {vm_name} --snapshotname {snap_name}",
        check=False,
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"Failed to revert to snapshot '{snap_name}': {r.stderr or r.stdout}"
        )
    print(f"  Snapshot '{snap_name}' restored.")

    kubeconfig_dest = Path(kubeconfig_local_path)
    kubeconfig_dest.parent.mkdir(parents=True, exist_ok=True)
    scp_cmd(
        f"{user}@{host}:{SNAPSHOT_DIR}/{snap_name}.kubeconfig",
        str(kubeconfig_dest),
    )
    print(f"  Kubeconfig restored to {kubeconfig_dest}")


def delete_snapshot(
    host: str,
    user: str,
    vm_name: str,
    ocp_version: str,
) -> None:
    """Delete a snapshot and its saved kubeconfig."""
    snap_name = get_snapshot_name(ocp_version)

    r = ssh_cmd(
        host, user,
        f"virsh snapshot-delete {vm_name} --snapshotname {snap_name}",
        check=False,
        timeout=120,
    )
    if r.returncode != 0 and "not found" not in (r.stderr or "").lower():
        print(f"  Warning: failed to delete snapshot '{snap_name}': {r.stderr}")

    ssh_cmd(
        host, user,
        f"rm -f {SNAPSHOT_DIR}/{snap_name}.kubeconfig",
        check=False,
    )


