#!/usr/bin/env python3
"""
Deploy or Delete a Single Node OpenShift (SNO) cluster using kcli.

Supports both local and remote libvirt hosts.

Usage:
  # Deploy to remote host (version and pull-secret are required)
  python main.py --version 4.20 --pull-secret-path /path/to/secret.json --remote user@host deploy
  
  # Delete cluster (local or remote)
  python main.py --remote user@host delete
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from config import get_kcli_params, print_config, CLUSTER_NAME
from params import update_version_to_latest_patch
from deploy import deploy_sno
from delete import delete_sno


def parse_remote_arg(remote: str | None) -> tuple[str | None, str]:
    """
    Parse the --remote argument into (host, user).
    Accepts formats: 'host', 'user@host'
    Returns (host, user) where user defaults to 'root' if not specified.
    """
    if not remote:
        return None, "root"
    
    if "@" in remote:
        user, host = remote.split("@", 1)
        return host, user
    else:
        return remote, "root"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage Single Node OpenShift (SNO) cluster with kcli.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Deploy to remote host
  %(prog)s --version 4.20 --pull-secret-path ~/keys/ps.json --remote root@myhost.example.com deploy
  
  # Delete cluster from remote host
  %(prog)s --remote root@myhost.example.com delete

Environment variables:
  OCP_CLUSTER_VERSION - OpenShift version to install (required for deploy)
  PULL_SECRET_PATH    - Path to pull secret file (required for deploy)
  SSH_KEY_PATH        - Path to SSH private key for remote connections (optional)
  PCI_DEVICES         - Comma or space-separated PCI device addresses for passthrough (optional)
  CTLPLANE_NUMCPUS    - Number of vCPUs per control plane node (default: 6)
  WORKER_NUMCPUS      - Number of vCPUs per worker node (default: 4)
  WAIT_TIMEOUT        - Max seconds to wait for cluster ready (default: 3600)
  NO_WAIT             - Set to 'true' to skip waiting for cluster ready
""",
    )
    parser.add_argument(
        "--version",
        dest="ocp_version",
        default=os.environ.get("OCP_CLUSTER_VERSION"),
        help="OpenShift version to install (e.g., 4.20 or 4.20.6). Required for deploy. (env: OCP_CLUSTER_VERSION)",
    )
    parser.add_argument(
        "--pull-secret-path",
        dest="pull_secret",
        default=os.environ.get("PULL_SECRET_PATH"),
        help="Path to pull secret file. Required for deploy. (env: PULL_SECRET_PATH)",
    )
    parser.add_argument(
        "--remote",
        metavar="[USER@]HOST",
        help="Remote libvirt host. Format: 'hostname' or 'user@hostname' (default user: root)",
    )
    parser.add_argument(
        "--ssh-key",
        dest="ssh_key",
        default=os.environ.get("SSH_KEY_PATH"),
        help="Path to SSH private key file for remote connections. (env: SSH_KEY_PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show configuration but do not run kcli commands.",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Don't wait for cluster to be ready (remote deployments only).",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=int(os.environ.get("WAIT_TIMEOUT", "3600")),
        help="Timeout in seconds waiting for cluster ready (default: 3600, env: WAIT_TIMEOUT)",
    )
    parser.add_argument(
        "--pci-device",
        dest="pci_devices",
        action="append",
        default=None,
        help="PCI device address for passthrough (e.g., 0000:b3:00.0). Can be specified multiple times. (env: PCI_DEVICES)",
    )
    parser.add_argument(
        "--ctlplane-numcpus",
        dest="ctlplane_numcpus",
        type=int,
        default=None,
        help="Number of vCPUs per control plane node (default: 6). (env: CTLPLANE_NUMCPUS)",
    )
    parser.add_argument(
        "--worker-numcpus",
        dest="worker_numcpus",
        type=int,
        default=None,
        help="Number of vCPUs per worker node (default: 4). (env: WORKER_NUMCPUS)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Action to perform")
    
    # Deploy command
    subparsers.add_parser("deploy", help="Deploy the SNO cluster")
    
    # Delete command
    subparsers.add_parser("delete", help="Delete the SNO cluster")

    args = parser.parse_args(argv)
    
    # Validation
    if args.command == "deploy":
        if not args.ocp_version:
            parser.error("deploy command requires --version or OCP_CLUSTER_VERSION env var")
        if not args.pull_secret:
            parser.error("deploy command requires --pull-secret-path or PULL_SECRET_PATH env var")
    
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    
    # Default to deploy if no command is specified
    command = args.command or "deploy"
    
    # Parse remote argument
    host, user = parse_remote_arg(args.remote)
    
    # Check for NO_WAIT environment variable
    no_wait = args.no_wait or os.environ.get("NO_WAIT", "").lower() == "true"

    if command == "deploy":
        # Get the OCP version (auto-update to latest patch if X.Y format)
        ocp_version = args.ocp_version
        ocp_version = update_version_to_latest_patch(ocp_version)
        
        # Get PCI devices from args or environment
        pci_devices = args.pci_devices
        if not pci_devices:
            env_pci = os.environ.get("PCI_DEVICES", "")
            if env_pci:
                # Support both comma and space delimiters
                pci_devices = [d.strip() for d in re.split(r"[,\s]+", env_pci) if d.strip()]
        
        # Get CPU counts from args or environment
        ctlplane_numcpus = args.ctlplane_numcpus
        if ctlplane_numcpus is None:
            env_ctlplane_numcpus = os.environ.get("CTLPLANE_NUMCPUS", "")
            if env_ctlplane_numcpus:
                ctlplane_numcpus = int(env_ctlplane_numcpus)
        
        worker_numcpus = args.worker_numcpus
        if worker_numcpus is None:
            env_worker_numcpus = os.environ.get("WORKER_NUMCPUS", "")
            if env_worker_numcpus:
                worker_numcpus = int(env_worker_numcpus)
        
        # Build parameters from config + CLI args
        params = get_kcli_params(
            tag=ocp_version,
            pull_secret=args.pull_secret,
            ctlplane_numcpus=ctlplane_numcpus,
            worker_numcpus=worker_numcpus,
        )
        
        # Print configuration
        print_config(params)
        if pci_devices:
            print(f"PCI Passthrough Devices: {pci_devices}")
        
        deploy_sno(
            params=params,
            dry_run=args.dry_run,
            remote_host=host,
            pci_devices=pci_devices,
            remote_user=user,
            wait_timeout=args.wait_timeout,
            no_wait=no_wait,
            ssh_key=args.ssh_key,
        )
        
    elif command == "delete":
        # For delete, we just need the cluster name from config
        params = {"cluster": CLUSTER_NAME}
        
        delete_sno(
            params=params,
            dry_run=args.dry_run,
            remote_host=host,
            remote_user=user,
            ssh_key=args.ssh_key,
        )
        
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
