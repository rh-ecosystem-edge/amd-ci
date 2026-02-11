"""
OpenShift Cluster Configuration.

Contains default constants, configuration dataclasses, and YAML config file loading.
Default configuration is Single Node OpenShift (SNO): 1 control plane, 0 workers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CLUSTER_NAME = "ocp"
DOMAIN = "example.com"
NETWORK = "default"

CTLPLANES = 1
WORKERS = 0
CTLPLANE_MEMORY = 18432  # MB
CTLPLANE_NUMCPUS = 6  # Minimum 6 vCPUs for KMM, AMD GPU Operator, and NFD Operator
WORKER_MEMORY = 16384  # MB
WORKER_NUMCPUS = 4
DISK_SIZE = 120  # GB

API_IP = "192.168.122.253"

VERSION_CHANNEL = "stable"

REMOTE_USER = "root"

# Deployment options
WAIT_TIMEOUT = 3600  # seconds

@dataclass
class RemoteConfig:
    """Remote deployment configuration."""

    host: str | None = None
    user: str = REMOTE_USER
    ssh_key_path: str | None = None


@dataclass
class NodeConfig:
    """Node resource configuration."""

    numcpus: int
    memory: int


@dataclass
class ClusterConfig:
    """Complete cluster configuration."""

    # Cluster identification
    ocp_version: str | None = None
    cluster_name: str = CLUSTER_NAME
    domain: str = DOMAIN

    # Node topology
    ctlplanes: int = CTLPLANES
    workers: int = WORKERS

    # Node resources
    ctlplane: NodeConfig = field(
        default_factory=lambda: NodeConfig(numcpus=CTLPLANE_NUMCPUS, memory=CTLPLANE_MEMORY)
    )
    worker: NodeConfig = field(
        default_factory=lambda: NodeConfig(numcpus=WORKER_NUMCPUS, memory=WORKER_MEMORY)
    )
    disk_size: int = DISK_SIZE

    # Network
    network: str = NETWORK
    api_ip: str = API_IP

    # Secrets
    pull_secret_path: str | None = None

    # Remote deployment
    remote: RemoteConfig = field(default_factory=RemoteConfig)

    # PCI passthrough
    pci_devices: list[str] = field(default_factory=list)

    # Deployment options
    wait_timeout: int = WAIT_TIMEOUT
    version_channel: str = VERSION_CHANNEL

def _expand_path(path: str | None) -> str | None:
    """Expand ~ and environment variables in a path."""
    if path is None:
        return None
    return os.path.expanduser(os.path.expandvars(path))


def get_kcli_params(config: ClusterConfig, tag: str) -> dict:
    """
    Build the kcli parameters dictionary from ClusterConfig.
    
    Args:
        config: ClusterConfig object with all settings
        tag: OpenShift version (e.g., "4.20.8") - may differ from config.ocp_version
              if auto-resolved to latest patch
        
    Returns:
        Dictionary of kcli parameters
    """
    return {
        "cluster": config.cluster_name,
        "domain": config.domain,
        "network": config.network,
        "ctlplanes": config.ctlplanes,
        "workers": config.workers,
        "ctlplane_memory": config.ctlplane.memory,
        "ctlplane_numcpus": config.ctlplane.numcpus,
        "worker_memory": config.worker.memory,
        "worker_numcpus": config.worker.numcpus,
        "disk_size": config.disk_size,
        "tag": tag,
        "pull_secret": config.pull_secret_path,
        "api_ip": config.api_ip,
        "version": config.version_channel,
    }


def get_cluster_topology_description(ctlplanes: int, workers: int) -> str:
    """
    Get a description of the cluster topology.
    
    Args:
        ctlplanes: Number of control plane nodes
        workers: Number of worker nodes
        
    Returns:
        Description string (e.g., "SNO (Single Node)", "3 control planes + 2 workers")
    """
    if ctlplanes == 1 and workers == 0:
        return "SNO (Single Node OpenShift)"
    else:
        return f"{ctlplanes} control plane(s) + {workers} worker(s)"


def print_config(params: dict) -> None:
    """Print the configuration in a readable format."""
    ctlplanes = params.get("ctlplanes", CTLPLANES)
    workers = params.get("workers", WORKERS)
    topology = get_cluster_topology_description(ctlplanes, workers)
    
    print("=" * 60)
    print(f"OpenShift Cluster Configuration [{topology}]")
    print("=" * 60)
    for key, value in params.items():
        print(f"  {key}: {value}")
    print("=" * 60)


def load_config_file(config_path: str | Path) -> dict[str, Any]:
    """
    Load configuration from a YAML file.

    Args:
        config_path: Path to the YAML configuration file

    Returns:
        Dictionary containing the configuration

    Raises:
        FileNotFoundError: If the config file doesn't exist
        yaml.YAMLError: If the file contains invalid YAML
    """
    config_path = Path(config_path).expanduser()

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    return config or {}


def parse_config(raw_config: dict[str, Any]) -> ClusterConfig:
    """
    Parse raw configuration dictionary into ClusterConfig.

    Args:
        raw_config: Dictionary from YAML file

    Returns:
        ClusterConfig object with parsed values
    """
    # Parse remote configuration
    remote_data = raw_config.get("remote", {}) or {}
    remote = RemoteConfig(
        host=remote_data.get("host"),
        user=remote_data.get("user", REMOTE_USER),
        ssh_key_path=_expand_path(remote_data.get("ssh_key_path")),
    )

    # Parse node configurations
    ctlplane_data = raw_config.get("ctlplane", {}) or {}
    ctlplane = NodeConfig(
        numcpus=ctlplane_data.get("numcpus", CTLPLANE_NUMCPUS),
        memory=ctlplane_data.get("memory", CTLPLANE_MEMORY),
    )

    worker_data = raw_config.get("worker", {}) or {}
    worker = NodeConfig(
        numcpus=worker_data.get("numcpus", WORKER_NUMCPUS),
        memory=worker_data.get("memory", WORKER_MEMORY),
    )

    # Parse PCI devices (ensure it's a list)
    pci_devices = raw_config.get("pci_devices", []) or []
    if isinstance(pci_devices, str):
        pci_devices = [d.strip() for d in pci_devices.replace(",", " ").split() if d.strip()]

    return ClusterConfig(
        ocp_version=raw_config.get("ocp_version"),
        cluster_name=raw_config.get("cluster_name", CLUSTER_NAME),
        domain=raw_config.get("domain", DOMAIN),
        ctlplanes=raw_config.get("ctlplanes", CTLPLANES),
        workers=raw_config.get("workers", WORKERS),
        ctlplane=ctlplane,
        worker=worker,
        disk_size=raw_config.get("disk_size", DISK_SIZE),
        network=raw_config.get("network", NETWORK),
        api_ip=raw_config.get("api_ip", API_IP),
        pull_secret_path=_expand_path(raw_config.get("pull_secret_path")),
        remote=remote,
        pci_devices=pci_devices,
        wait_timeout=raw_config.get("wait_timeout", WAIT_TIMEOUT),
        version_channel=raw_config.get("version_channel", VERSION_CHANNEL),
    )


def load_cluster_config(config_path: str | Path) -> ClusterConfig:
    """
    Load cluster configuration from a YAML file.

    Args:
        config_path: Path to YAML configuration file

    Returns:
        ClusterConfig object with loaded values
    """
    raw_config = load_config_file(config_path)
    return parse_config(raw_config)
