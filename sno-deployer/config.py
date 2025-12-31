"""
SNO Cluster Configuration Constants.

These are the default values for SNO cluster deployment.
The OCP version (tag) and pull_secret must be provided as parameters.
"""

# Cluster configuration
CLUSTER_NAME = "sno"
DOMAIN = "example.com"
NETWORK = "default"

# Node configuration
CTLPLANES = 1
WORKERS = 0
CTLPLANE_MEMORY = 18432  # MB
CTLPLANE_NUMCPUS = 6  # Minimum 6 vCPUs for KMM, AMD GPU Operator, and NFD Operator
WORKER_MEMORY = 16384  # MB
WORKER_NUMCPUS = 4
DISK_SIZE = 120  # GB

# Network configuration
API_IP = "192.168.122.253"

# OCP channel
VERSION_CHANNEL = "stable"


def get_kcli_params(
    tag: str,
    pull_secret: str,
    ctlplane_numcpus: int | None = None,
    worker_numcpus: int | None = None,
) -> dict:
    """
    Build the kcli parameters dictionary.
    
    Args:
        tag: OpenShift version (e.g., "4.20" or "4.20.6")
        pull_secret: Path to pull secret file
        ctlplane_numcpus: Number of vCPUs for control plane (defaults to CTLPLANE_NUMCPUS)
        worker_numcpus: Number of vCPUs for worker nodes (defaults to WORKER_NUMCPUS)
        
    Returns:
        Dictionary of kcli parameters
    """
    return {
        "cluster": CLUSTER_NAME,
        "domain": DOMAIN,
        "network": NETWORK,
        "ctlplanes": CTLPLANES,
        "workers": WORKERS,
        "ctlplane_memory": CTLPLANE_MEMORY,
        "ctlplane_numcpus": ctlplane_numcpus if ctlplane_numcpus is not None else CTLPLANE_NUMCPUS,
        "worker_memory": WORKER_MEMORY,
        "worker_numcpus": worker_numcpus if worker_numcpus is not None else WORKER_NUMCPUS,
        "disk_size": DISK_SIZE,
        "tag": tag,
        "pull_secret": pull_secret,
        "api_ip": API_IP,
        "version": VERSION_CHANNEL,
    }


def print_config(params: dict) -> None:
    """Print the configuration in a readable format."""
    print("=" * 60)
    print("SNO Cluster Configuration")
    print("=" * 60)
    for key, value in params.items():
        print(f"  {key}: {value}")
    print("=" * 60)

