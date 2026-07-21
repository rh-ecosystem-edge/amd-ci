#!/usr/bin/env python3
"""
Manage OpenShift cluster lifecycle with kcli.

Each command is responsible for a single task and does NOT trigger the next one.

Usage:
  python main.py --config cluster-config.yaml deploy       # deploy cluster (no operators, no tests)
  python main.py --config cluster-config.yaml operators     # install AMD GPU operators (no tests)
  python main.py --config cluster-config.yaml test-gpu      # run AMD GPU tests only
  python main.py --config cluster-config.yaml cleanup       # remove AMD GPU operator stack
  python main.py --config cluster-config.yaml delete        # delete the cluster
  python main.py --config cluster-config.yaml stop          # power off VMs (keep disk + snapshots)
  python main.py --config cluster-config.yaml must-gather   # collect diagnostic data
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add the repo root to sys.path so that imports like "from operators.main import ..."
# work when this script is invoked as "python3 cluster-provision/main.py".
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    ClusterConfig,
    get_kcli_params,
    load_cluster_config,
    print_config,
)
from params import update_version_to_latest_patch
from common import DeployError
from deploy import deploy_cluster
from delete import delete_cluster


def _kubeconfig_path(cluster_name: str) -> Path:
    return Path.home() / ".kcli" / "clusters" / cluster_name / "auth" / "kubeconfig"


def _snapshot_cluster_name(base_name: str, ocp_version: str) -> str:
    """Build a version-specific cluster name for snapshot caching.

    e.g. ("ocp", "4.22") -> "ocp-422", ("ocp", "4.22.5") -> "ocp-422"
    Uses only major.minor so all patch versions share the same cached cluster.
    """
    parts = ocp_version.split(".")
    if len(parts) < 2:
        raise ValueError(
            f"OCP version must be at least major.minor (got '{ocp_version}')"
        )
    return f"{base_name}-{parts[0]}{parts[1]}"


def _get_oc_runner(config: ClusterConfig):
    """Build an OcRunner appropriate for the config (local or remote)."""
    from shared.oc_runner import LocalOcRunner

    if config.remote.host and config.remote.ssh_key_path:
        from shared.ssh import set_ssh_key_path
        set_ssh_key_path(config.remote.ssh_key_path)

    if config.remote.host:
        from shared.oc_runner import RemoteOcRunner, REMOTE_KUBECONFIG
        return RemoteOcRunner(
            host=config.remote.host,
            user=config.remote.user,
            remote_kubeconfig=REMOTE_KUBECONFIG,
        )

    kubeconfig = _kubeconfig_path(config.cluster_name)
    if not kubeconfig.exists():
        raise FileNotFoundError(f"kubeconfig not found at {kubeconfig}")
    return LocalOcRunner(kubeconfig)


def _write_artifact(name: str, value: str) -> None:
    artifact_dir = os.environ.get("ARTIFACT_DIR")
    if artifact_dir:
        artifact_path = Path(artifact_dir)
        artifact_path.mkdir(parents=True, exist_ok=True)
        (artifact_path / name).write_text(value)
        print(f"Wrote {name}: {value}")


# ── Snapshot helpers ─────────────────────────────────────────────

def _list_cached_clusters(host: str, user: str, base_name: str) -> list[str]:
    """List version-specific cluster names present on the remote host.

    Finds VMs matching ``{base_name}-<digits>-ctlplane-0`` and returns
    the cluster name portion (e.g. ``["ocp-420", "ocp-421", "ocp-422"]``),
    sorted alphabetically (oldest version first in typical usage).

    Ignores the legacy base-name cluster (e.g. ``ocp-ctlplane-0``) since
    it was created before multi-version caching.
    """
    from shared.ssh import ssh_cmd
    import re

    r = ssh_cmd(host, user, "virsh list --all --name", check=False)
    if r.returncode != 0 or not r.stdout:
        return []
    clusters: set[str] = set()
    prefix = f"{base_name}-"
    for line in r.stdout.strip().splitlines():
        vm = line.strip()
        if vm.startswith(prefix) and "-ctlplane-" in vm:
            cluster = vm.rsplit("-ctlplane-", 1)[0]
            if cluster != base_name:
                clusters.add(cluster)
    return sorted(clusters)



def _stop_running_clusters(
    host: str,
    user: str,
    base_name: str,
    exclude: str | None = None,
) -> None:
    """Shut down any running cached cluster VMs (except *exclude*).

    Stops both ctlplane and bootstrap VMs so that stale bootstraps
    don't hold the shared API VIP and interfere with the next deploy.
    """
    from vm import shutdown_vm, destroy_vm, vm_state as get_vm_state

    for cluster in _list_cached_clusters(host, user, base_name):
        if cluster == exclude:
            continue
        vm = f"{cluster}-ctlplane-0"
        if get_vm_state(host, user, vm) == "running":
            print(f"  Stopping running cached cluster {cluster}...")
            shutdown_vm(host, user, vm)
        bootstrap = f"{cluster}-bootstrap"
        if get_vm_state(host, user, bootstrap) == "running":
            print(f"  Stopping bootstrap VM {bootstrap}...")
            destroy_vm(host, user, bootstrap)


def _evict_cached_clusters(
    host: str,
    user: str,
    base_name: str,
    max_cached: int,
    exclude: str | None = None,
) -> None:
    """Delete the oldest cached clusters when count exceeds *max_cached*.

    Uses ``kcli delete cluster`` so VMs, disks, and snapshots are removed.
    """
    from remote import get_kcli_client_name
    from common import run

    clusters = _list_cached_clusters(host, user, base_name)
    if exclude and exclude in clusters:
        clusters = [c for c in clusters if c != exclude]

    # Need to make room: we're about to add one, so evict until we have
    # at most (max_cached - 1) existing clusters.
    while len(clusters) >= max_cached:
        victim = clusters.pop(0)
        print(f"  Evicting cached cluster: {victim}")
        kcli_client = get_kcli_client_name(host)
        result = run(
            ["kcli", "-C", kcli_client, "delete", "cluster", victim, "--yes"],
            check=False,
        )
        if result.returncode != 0:
            print(f"  Warning: failed to evict {victim} (rc={result.returncode})")
            continue
        import shutil
        local_dir = Path.home() / ".kcli" / "clusters" / victim
        if local_dir.is_dir():
            shutil.rmtree(local_dir)


# ── Deploy (with snapshot support) ──────────────────────────────

def _deploy_with_snapshot(config: ClusterConfig, ocp_version: str) -> None:
    """Deploy via snapshot restore (cache hit) or full deploy + snapshot create (cache miss).

    Each OCP minor version gets its own cluster/VM (e.g. ``ocp-422``),
    allowing up to ``max_cached`` versions to coexist on the host.
    Only one cluster runs at a time (they share the same API IP).

    Flow on cache hit:
      1. Stop any other running cached cluster
      2. Revert VM to snapshot
      3. Attach PCI devices + start VM
      4. Wait for cluster API

    Flow on cache miss:
      1. Stop any running cached cluster
      2. Evict oldest clusters if at capacity
      3. Full kcli deploy (WITHOUT PCI — keep snapshot PCI-clean)
      4. Install base operators (NFD, KMM, MachineConfig + reboot)
      5. Shut down VM → create snapshot → attach PCI → start VM
      6. Wait for cluster API
    """
    from remote import (
        setup_remote_libvirt,
        configure_kcli_remote_client,
        setup_remote_cluster_access,
        wait_for_cluster_ready,
        get_cluster_status,
        print_access_instructions,
        set_ssh_key_path,
    )
    from snapshot import find_snapshot, create_snapshot, revert_snapshot
    from vm import (
        attach_pci_devices,
        fix_container_storage,
        shutdown_vms,
        start_vms,
        detach_all_pci_devices,
    )

    host = config.remote.host
    user = config.remote.user
    cluster_name = config.cluster_name   # already version-specific (e.g. ocp-422)
    base_name = cluster_name.rsplit("-", 1)[0]  # original base name (e.g. ocp)
    ctlplanes = config.ctlplanes
    vm_name = f"{cluster_name}-ctlplane-0"
    kubeconfig = _kubeconfig_path(cluster_name)

    if config.remote.ssh_key_path:
        set_ssh_key_path(config.remote.ssh_key_path)

    print(f"\n{'='*60}")
    print("Deploy with Snapshot Caching")
    print(f"{'='*60}")
    print(f"  Remote Host: {user}@{host}")
    print(f"  OCP Version: {ocp_version}")
    print(f"  Cluster Name: {cluster_name}")
    print(f"  Snapshot cache: max {config.snapshot.max_cached} versions")
    print(f"{'='*60}\n")

    setup_remote_libvirt(host, user)
    kcli_client = configure_kcli_remote_client(host, user)

    if find_snapshot(host, user, vm_name, ocp_version):
        # ── CACHE HIT ──
        print(f"\nSnapshot found for OCP {ocp_version} — restoring...")

        _stop_running_clusters(host, user, base_name, exclude=cluster_name)

        revert_snapshot(host, user, vm_name, ocp_version, str(kubeconfig))

        if config.pci_devices:
            print("\nAttaching PCI devices after snapshot restore...")
            attach_pci_devices(host, user, vm_name, config.pci_devices)
        else:
            from vm import start_vm
            start_vm(host, user, vm_name)

        setup_remote_cluster_access(
            host, user, cluster_name, config.api_ip, config.domain,
        )
        wait_for_cluster_ready(host, user, config.api_ip, config.wait_timeout)

    else:
        # ── CACHE MISS ──
        print(f"\nNo snapshot for OCP {ocp_version} — full deploy...")

        _stop_running_clusters(host, user, base_name)
        _evict_cached_clusters(
            host, user, base_name,
            max_cached=config.snapshot.max_cached,
            exclude=cluster_name,
        )

        params = get_kcli_params(config, ocp_version)

        deploy_cluster(
            params=params,
            remote_host=host,
            pci_devices=None,
            remote_user=user,
            wait_timeout=config.wait_timeout,
            ssh_key=config.remote.ssh_key_path,
        )

        print("\nInstalling base operators for snapshot...")
        oc = _get_oc_runner(config)
        from operators.main import install_base, OperatorInstallConfig

        machine_config_role = config.operators.machine_config_role
        if config.ctlplanes == 1 and config.workers == 0:
            machine_config_role = "master"

        op_config = OperatorInstallConfig(
            machine_config_role=machine_config_role,
            ocp_version=ocp_version,
        )
        install_base(oc, config=op_config)

        if hasattr(oc, "close"):
            oc.close()

        print("\nCreating snapshot...")
        shutdown_vms(host, user, cluster_name, ctlplanes)
        detach_all_pci_devices(host, user, vm_name)

        create_snapshot(
            host, user, vm_name, ocp_version,
            kubeconfig_local_path=str(kubeconfig),
        )

        if config.pci_devices:
            print("\nAttaching PCI devices...")
            attach_pci_devices(host, user, vm_name, config.pci_devices)
        else:
            start_vms(host, user, cluster_name, ctlplanes)

        wait_for_cluster_ready(host, user, config.api_ip, config.wait_timeout)

    status = get_cluster_status(host, user)
    print("\n" + "=" * 60)
    print("CLUSTER STATUS")
    print("=" * 60)
    print(status)

    print_access_instructions(
        host=host, user=user, cluster_name=cluster_name,
        api_ip=config.api_ip, domain=config.domain,
        kcli_client=kcli_client,
    )
    print("\nDeploy (snapshot) completed successfully!")


# ── Commands ────────────────────────────────────────────────────

def cmd_deploy(config: ClusterConfig, config_file: str) -> int:
    ocp_version = update_version_to_latest_patch(
        config.ocp_version, config.version_channel,
    )

    is_sno = config.ctlplanes == 1 and config.workers == 0
    use_snapshot = config.snapshot.enabled and is_sno and config.remote.host

    if config.snapshot.enabled and not use_snapshot:
        if not is_sno:
            print("Warning: snapshot caching is only supported for SNO clusters. "
                  "Falling back to full deploy.")
        elif not config.remote.host:
            print("Warning: snapshot caching requires a remote host. "
                  "Falling back to full deploy.")
        config.snapshot.enabled = False

    if use_snapshot:
        _deploy_with_snapshot(config, ocp_version)
    else:
        params = get_kcli_params(config, ocp_version)
        print_config(params)
        if config.pci_devices:
            print(f"PCI Passthrough Devices: {config.pci_devices}")
        print(f"Config file: {config_file}")

        actual_version = deploy_cluster(
            params=params,
            remote_host=config.remote.host,
            pci_devices=config.pci_devices,
            remote_user=config.remote.user,
            wait_timeout=config.wait_timeout,
            ssh_key=config.remote.ssh_key_path,
        )

        if actual_version and actual_version != ocp_version:
            print(
                f"Error: version mismatch — requested {ocp_version} but "
                f"cluster deployed {actual_version}.",
                file=sys.stderr,
            )
            return 1

    _write_artifact("ocp.version", ocp_version)
    return 0


def cmd_operators(config: ClusterConfig) -> int:
    from operators.main import (
        install_gpu_operator,
        install_operators,
        OperatorInstallConfig,
    )
    from operators.version_resolver import resolve_latest_patch

    oc = _get_oc_runner(config)
    gpu_version = resolve_latest_patch(config.operators.gpu_operator_version)

    machine_config_role = config.operators.machine_config_role
    if config.ctlplanes == 1 and config.workers == 0:
        machine_config_role = "master"

    op_config = OperatorInstallConfig(
        machine_config_role=machine_config_role,
        gpu_operator_version=gpu_version,
        driver_version=config.operators.driver_version,
        enable_metrics=config.operators.enable_metrics,
        ocp_version=config.ocp_version,
        use_source_image=config.operators.use_source_image,
    )

    is_sno = config.ctlplanes == 1 and config.workers == 0
    if config.snapshot.enabled and config.remote.host and is_sno:
        install_gpu_operator(oc, config=op_config)
    else:
        install_operators(oc, config=op_config)

    _write_artifact("operator.version", gpu_version)

    if hasattr(oc, "close"):
        oc.close()
    return 0


def cmd_stop(config: ClusterConfig) -> int:
    """Power off cluster VMs without deleting disks or snapshots."""
    if not config.remote.host:
        print("Error: 'stop' is only supported for remote deployments.", file=sys.stderr)
        return 1

    if config.remote.ssh_key_path:
        from shared.ssh import set_ssh_key_path
        set_ssh_key_path(config.remote.ssh_key_path)

    from vm import shutdown_vm, destroy_vm, vm_exists

    host = config.remote.host
    user = config.remote.user
    cluster_name = config.cluster_name
    ctlplanes = config.ctlplanes

    print(f"Stopping cluster VMs on {host}...")
    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"
        if vm_exists(host, user, vm_name):
            shutdown_vm(host, user, vm_name)
            print(f"  {vm_name} stopped.")
        else:
            print(f"  {vm_name} not found — skipping.")

    bootstrap_vm = f"{cluster_name}-bootstrap"
    if vm_exists(host, user, bootstrap_vm):
        destroy_vm(host, user, bootstrap_vm)
        print(f"  {bootstrap_vm} stopped.")

    print("Cluster VMs stopped (disk and snapshots preserved).")
    return 0


def cmd_delete(config: ClusterConfig) -> int:
    if config.snapshot.enabled and config.remote.host:
        base_name = config.cluster_name.rsplit("-", 1)[0]
        cached = _list_cached_clusters(
            config.remote.host, config.remote.user, base_name,
        )
        if cached:
            print(f"Deleting all cached clusters: {', '.join(cached)}")
            for cluster in cached:
                delete_cluster(
                    params={"cluster": cluster},
                    remote_host=config.remote.host,
                    remote_user=config.remote.user,
                    ssh_key=config.remote.ssh_key_path,
                )
            return 0
        print("No cached clusters found.")

    params = {"cluster": config.cluster_name}
    delete_cluster(
        params=params,
        remote_host=config.remote.host,
        remote_user=config.remote.user,
        ssh_key=config.remote.ssh_key_path,
    )
    return 0


def cmd_test_gpu(config: ClusterConfig) -> int:
    from tests.amd_gpu.runner import run_gpu_tests, run_gpu_tests_remote

    kubeconfig_path = _kubeconfig_path(config.cluster_name)
    if not kubeconfig_path.exists():
        print(f"Error: kubeconfig not found at {kubeconfig_path}", file=sys.stderr)
        return 1

    if config.remote.host:
        return run_gpu_tests_remote(
            config.remote.host,
            config.remote.user,
            kubeconfig_path,
            ssh_key_path=config.remote.ssh_key_path,
        )
    return run_gpu_tests(kubeconfig_path)


def cmd_cleanup(config: ClusterConfig) -> int:
    from operators.cleanup import cleanup_operators

    oc = _get_oc_runner(config)
    cleanup_operators(oc)
    if hasattr(oc, "close"):
        oc.close()
    return 0


def cmd_must_gather(config: ClusterConfig) -> int:
    from must_gather import run_must_gather, run_must_gather_remote

    artifact_dir = config.must_gather.artifact_dir

    if config.remote.host and config.remote.ssh_key_path:
        from shared.ssh import set_ssh_key_path
        set_ssh_key_path(config.remote.ssh_key_path)

    if config.remote.host:
        return run_must_gather_remote(
            host=config.remote.host,
            user=config.remote.user,
            artifact_dir=artifact_dir,
        )

    kubeconfig = _kubeconfig_path(config.cluster_name)
    if not kubeconfig.exists():
        print(f"Error: kubeconfig not found at {kubeconfig}", file=sys.stderr)
        return 1
    return run_must_gather(kubeconfig=str(kubeconfig), artifact_dir=artifact_dir)


# ── CLI ─────────────────────────────────────────────────────────

COMMANDS = {
    "deploy": cmd_deploy,
    "delete": cmd_delete,
    "operators": cmd_operators,
    "test-gpu": cmd_test_gpu,
    "cleanup": cmd_cleanup,
    "stop": cmd_stop,
    "must-gather": cmd_must_gather,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage OpenShift cluster with kcli.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --config cluster-config.yaml deploy
  %(prog)s --config cluster-config.yaml delete
""",
    )

    parser.add_argument(
        "-c", "--config",
        dest="config_file",
        required=True,
        help="Path to YAML configuration file.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Action to perform")
    subparsers.add_parser("deploy", help="Deploy the OpenShift cluster (with optional snapshot caching)")
    subparsers.add_parser("delete", help="Delete the OpenShift cluster (removes VMs, disks, and snapshots)")
    subparsers.add_parser("stop", help="Power off cluster VMs (preserve disks and snapshots)")
    subparsers.add_parser(
        "operators",
        help="Install AMD GPU Operator (cluster must already exist)",
    )
    subparsers.add_parser(
        "test-gpu",
        help="Run AMD GPU verification tests (cluster must be ready with operators installed)",
    )
    subparsers.add_parser(
        "cleanup",
        help="Clean up AMD GPU Operator stack (reverse of operators install)",
    )
    subparsers.add_parser(
        "must-gather",
        help="Collect diagnostic data (NFD, AMD GPU Operator, KMM)",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command
    if not command:
        valid = ", ".join(COMMANDS)
        print(f"Error: no command specified. Use one of: {valid}", file=sys.stderr)
        return 1

    try:
        config = load_cluster_config(args.config_file)
    except (FileNotFoundError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if config.snapshot.enabled:
        config.cluster_name = _snapshot_cluster_name(
            config.cluster_name, config.ocp_version,
        )

    handler = COMMANDS.get(command)
    if not handler:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 1

    try:
        if command == "deploy":
            return handler(config, args.config_file)
        return handler(config)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
