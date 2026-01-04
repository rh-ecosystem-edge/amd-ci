"""
OpenShift Cluster Configuration Constants.

These are the default values for cluster deployment.
Default configuration is Single Node OpenShift (SNO): 1 control plane, 0 workers.
The OCP version (tag) and pull_secret must be provided as parameters.
"""

# Cluster configuration
CLUSTER_NAME = "ocp"
DOMAIN = "example.com"
NETWORK = "default"

# Node configuration (defaults to SNO: 1 control plane, 0 workers)
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
    cluster_name: str | None = None,
    ctlplanes: int | None = None,
    workers: int | None = None,
    ctlplane_numcpus: int | None = None,
    worker_numcpus: int | None = None,
) -> dict:
    """
    Build the kcli parameters dictionary.
    
    Args:
        tag: OpenShift version (e.g., "4.20" or "4.20.6")
        pull_secret: Path to pull secret file
        cluster_name: Name of the cluster (defaults to CLUSTER_NAME="ocp")
        ctlplanes: Number of control plane nodes (defaults to CTLPLANES=1 for SNO)
        workers: Number of worker nodes (defaults to WORKERS=0 for SNO)
        ctlplane_numcpus: Number of vCPUs for control plane (defaults to CTLPLANE_NUMCPUS)
        worker_numcpus: Number of vCPUs for worker nodes (defaults to WORKER_NUMCPUS)
        
    Returns:
        Dictionary of kcli parameters
    """
    return {
        "cluster": cluster_name if cluster_name is not None else CLUSTER_NAME,
        "domain": DOMAIN,
        "network": NETWORK,
        "ctlplanes": ctlplanes if ctlplanes is not None else CTLPLANES,
        "workers": workers if workers is not None else WORKERS,
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
