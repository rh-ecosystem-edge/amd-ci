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


# ── Deploy (with snapshot support) ──────────────────────────────

def _deploy_with_snapshot(config: ClusterConfig, ocp_version: str) -> None:
    """Deploy via snapshot restore (cache hit) or full deploy + snapshot create (cache miss).

    Flow on cache hit:
      1. Revert VM to snapshot
      2. Attach PCI devices + start VM
      3. Wait for cluster API

    Flow on cache miss:
      1. Full kcli deploy (WITHOUT PCI — keep snapshot PCI-clean)
      2. Install base operators (NFD, KMM, MachineConfig + reboot)
      3. Shut down VM → create snapshot → attach PCI → start VM
      4. Wait for cluster API
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
        destroy_vm,
        fix_container_storage,
        shutdown_vms,
        start_vms,
        detach_all_pci_devices,
        shutdown_vm,
    )
    from deploy import push_ssh_key_to_remote

    host = config.remote.host
    user = config.remote.user
    cluster_name = config.cluster_name
    ctlplanes = config.ctlplanes
    vm_name = f"{cluster_name}-ctlplane-0"
    kubeconfig = _kubeconfig_path(cluster_name)

    if config.remote.ssh_key_path:
        set_ssh_key_path(config.remote.ssh_key_path)

    print(f"\n{'='*60}")
    print(f"Deploy with Snapshot Caching")
    print(f"{'='*60}")
    print(f"  Remote Host: {user}@{host}")
    print(f"  OCP Version: {ocp_version}")
    print(f"  Snapshot cache: max {config.snapshot.max_cached} versions")
    print(f"{'='*60}\n")

    setup_remote_libvirt(host, user)
    kcli_client = configure_kcli_remote_client(host, user)

    if find_snapshot(host, user, vm_name, ocp_version):
        # ── CACHE HIT ──
        print(f"\nSnapshot found for OCP {ocp_version} — restoring...")

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

        params = get_kcli_params(config, ocp_version)

        # Deploy WITHOUT PCI so the snapshot is PCI-clean
        deploy_cluster(
            params=params,
            remote_host=host,
            pci_devices=None,
            remote_user=user,
            wait_timeout=config.wait_timeout,
            ssh_key=config.remote.ssh_key_path,
        )

        # Run base operator install (GPU-version-independent)
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

        # Shut down VM, detach PCI (in case deploy attached any), create snapshot
        print("\nCreating snapshot...")
        shutdown_vms(host, user, cluster_name, ctlplanes)
        detach_all_pci_devices(host, user, vm_name)

        create_snapshot(
            host, user, vm_name, ocp_version,
            kubeconfig_local_path=str(kubeconfig),
            max_cached=config.snapshot.max_cached,
        )

        # Attach PCI and start
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
    if config.snapshot.enabled and config.remote.host and is_sno:
        _deploy_with_snapshot(config, ocp_version)
    elif config.snapshot.enabled and not is_sno:
        print("Warning: snapshot caching is only supported for SNO clusters. "
              "Falling back to full deploy.")
        config.snapshot.enabled = False  # disable for operators step too
    else:
        params = get_kcli_params(config, ocp_version)
        print_config(params)
        if config.pci_devices:
            print(f"PCI Passthrough Devices: {config.pci_devices}")
        print(f"Config file: {config_file}")

        deploy_cluster(
            params=params,
            remote_host=config.remote.host,
            pci_devices=config.pci_devices,
            remote_user=config.remote.user,
            wait_timeout=config.wait_timeout,
            ssh_key=config.remote.ssh_key_path,
        )

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
    )

    if config.snapshot.enabled:
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

    from vm import shutdown_vm, vm_exists

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

    print("Cluster VMs stopped (disk and snapshots preserved).")
    return 0


def cmd_delete(config: ClusterConfig) -> int:
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

    handler = COMMANDS.get(command)
    if not handler:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 1

    try:
        if command == "deploy":
            return handler(config, args.config_file)
        return handler(config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
