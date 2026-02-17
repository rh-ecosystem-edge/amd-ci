#!/usr/bin/env python3
"""
Deploy or Delete an OpenShift cluster using kcli.

Usage:
  python main.py --config cluster-config.yaml deploy
  python main.py --config cluster-config.yaml delete
"""

from __future__ import annotations

import argparse
import sys

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

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command
    
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
        
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
