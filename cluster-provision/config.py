"""
OpenShift Cluster Configuration.

Configuration dataclasses and YAML config file loading.
All values must be provided in the YAML config file — no implicit defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VERSION_CHANNEL = "stable"


@dataclass
class RemoteConfig:
    """Remote deployment configuration."""

    host: str | None
    user: str
    ssh_key_path: str | None


@dataclass
class NodeConfig:
    """Node resource configuration."""

    numcpus: int
    memory: int


@dataclass
class ClusterConfig:
    """Complete cluster configuration.

    All fields are required and must be set explicitly in the YAML config file.
    """

    ocp_version: str
    pull_secret_path: str
    cluster_name: str
    domain: str
    ctlplanes: int
    workers: int
    ctlplane: NodeConfig
    worker: NodeConfig
    disk_size: int
    network: str
    api_ip: str
    remote: RemoteConfig
    pci_devices: list[str]
    wait_timeout: int
    version_channel: str


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
    ctlplanes = params["ctlplanes"]
    workers = params["workers"]
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

    Every required key must be present in the YAML; missing keys raise an error.

    Args:
        raw_config: Dictionary from YAML file

    Returns:
        ClusterConfig object with parsed values

    Raises:
        KeyError: If any required configuration key is missing
    """
    try:
        remote_data = raw_config["remote"]
        remote = RemoteConfig(
            host=remote_data.get("host"),
            user=remote_data["user"],
            ssh_key_path=_expand_path(remote_data.get("ssh_key_path")),
        )

        ctlplane_data = raw_config["ctlplane"]
        ctlplane = NodeConfig(
            numcpus=ctlplane_data["numcpus"],
            memory=ctlplane_data["memory"],
        )

        worker_data = raw_config["worker"]
        worker = NodeConfig(
            numcpus=worker_data["numcpus"],
            memory=worker_data["memory"],
        )

        pci_devices = raw_config["pci_devices"] or []
        if isinstance(pci_devices, str):
            pci_devices = [d.strip() for d in pci_devices.replace(",", " ").split() if d.strip()]

        return ClusterConfig(
            ocp_version=raw_config["ocp_version"],
            pull_secret_path=_expand_path(raw_config["pull_secret_path"]),
            cluster_name=raw_config["cluster_name"],
            domain=raw_config["domain"],
            ctlplanes=raw_config["ctlplanes"],
            workers=raw_config["workers"],
            ctlplane=ctlplane,
            worker=worker,
            disk_size=raw_config["disk_size"],
            network=raw_config["network"],
            api_ip=raw_config["api_ip"],
            remote=remote,
            pci_devices=pci_devices,
            wait_timeout=raw_config["wait_timeout"],
            version_channel=raw_config["version_channel"],
        )
    except KeyError as exc:
        raise KeyError(
            f"Missing required config key: {exc}. "
            f"See cluster-config.yaml.example for all required fields."
        ) from exc


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
