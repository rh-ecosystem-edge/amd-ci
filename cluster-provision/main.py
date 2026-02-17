#!/usr/bin/env python3
"""
Manage OpenShift cluster lifecycle with kcli.

Each command is responsible for a single task and does NOT trigger the next one.

Usage:
  python main.py --config cluster-config.yaml deploy      # deploy cluster (no operators, no tests)
  python main.py --config cluster-config.yaml operators    # install AMD GPU operators (no tests)
  python main.py --config cluster-config.yaml cleanup      # remove AMD GPU operator stack
  python main.py --config cluster-config.yaml delete       # delete the cluster
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add the repo root to sys.path so that imports like "from operators.main import ..."
# work when this script is invoked as "python3 cluster-provision/main.py".
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    get_kcli_params,
    load_cluster_config,
    print_config,
)
from params import update_version_to_latest_patch
from deploy import deploy_cluster
from delete import delete_cluster


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
    subparsers.add_parser("deploy", help="Deploy the OpenShift cluster")
    subparsers.add_parser("delete", help="Delete the OpenShift cluster")
    subparsers.add_parser(
        "operators",
        help="Run only AMD GPU Operator and dependencies install (cluster must already exist)",
    )
    subparsers.add_parser(
        "cleanup",
        help="Clean up AMD GPU Operator stack (reverse of operators install)",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command
    if not command:
        print("Error: no command specified. Use one of: deploy, delete, operators, cleanup", file=sys.stderr)
        return 1

    # Load configuration from file
    try:
        config = load_cluster_config(args.config_file)
    except (FileNotFoundError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    
    if command == "deploy":
        # Get the OCP version (auto-update to latest patch if X.Y format)
        ocp_version = update_version_to_latest_patch(config.ocp_version, config.version_channel)
        
        # Build parameters from config
        params = get_kcli_params(config, ocp_version)
        
        # Print configuration
        print_config(params)
        if config.pci_devices:
            print(f"PCI Passthrough Devices: {config.pci_devices}")
        print(f"Config file: {args.config_file}")
        
        deploy_cluster(
            params=params,
            remote_host=config.remote.host,
            pci_devices=config.pci_devices,
            remote_user=config.remote.user,
            wait_timeout=config.wait_timeout,
            ssh_key=config.remote.ssh_key_path,
        )
        
    elif command == "delete":
        params = {"cluster": config.cluster_name}
        
        delete_cluster(
            params=params,
            remote_host=config.remote.host,
            remote_user=config.remote.user,
            ssh_key=config.remote.ssh_key_path,
        )

    elif command == "operators":
        from operators.main import install_operators, OperatorInstallConfig
        from shared.oc_runner import LocalOcRunner

        if config.remote.host and config.remote.ssh_key_path:
            from shared.ssh import set_ssh_key_path
            set_ssh_key_path(config.remote.ssh_key_path)

        if config.remote.host:
            from shared.oc_runner import RemoteOcRunner, REMOTE_KUBECONFIG
            oc = RemoteOcRunner(host=config.remote.host, user=config.remote.user, remote_kubeconfig=REMOTE_KUBECONFIG)
        else:
            kubeconfig = (
                Path.home()
                / ".kcli"
                / "clusters"
                / config.cluster_name
                / "auth"
                / "kubeconfig"
            )
            if not kubeconfig.exists():
                print(f"Error: kubeconfig not found at {kubeconfig}", file=sys.stderr)
                return 1
            oc = LocalOcRunner(kubeconfig)
        machine_config_role = config.operators.machine_config_role
        if config.ctlplanes == 1 and config.workers == 0:
            machine_config_role = "master"
        op_config = OperatorInstallConfig(
            machine_config_role=machine_config_role,
            driver_version=config.operators.driver_version,
            enable_metrics=config.operators.enable_metrics,
            ocp_version=config.ocp_version,
        )
        install_operators(oc, config=op_config)

    elif command == "cleanup":
        from operators.cleanup import cleanup_operators
        from shared.oc_runner import LocalOcRunner

        if config.remote.host and config.remote.ssh_key_path:
            from shared.ssh import set_ssh_key_path
            set_ssh_key_path(config.remote.ssh_key_path)

        if config.remote.host:
            from shared.oc_runner import RemoteOcRunner, REMOTE_KUBECONFIG
            oc = RemoteOcRunner(host=config.remote.host, user=config.remote.user, remote_kubeconfig=REMOTE_KUBECONFIG)
        else:
            kubeconfig = (
                Path.home()
                / ".kcli"
                / "clusters"
                / config.cluster_name
                / "auth"
                / "kubeconfig"
            )
            if not kubeconfig.exists():
                print(f"Error: kubeconfig not found at {kubeconfig}", file=sys.stderr)
                return 1
            oc = LocalOcRunner(kubeconfig)
        cleanup_operators(oc)

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
