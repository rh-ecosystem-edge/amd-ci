# AMD CI

Continuous Integration for AMD GPU Operator on OpenShift.

## OpenShift Cluster Provisioner

Deploy OpenShift clusters using kcli on local or remote libvirt hosts.

### Quick Start

```bash
# 1. Copy the example config
cp cluster-config.yaml.example cluster-config.yaml

# 2. Edit with your settings
vim cluster-config.yaml

# 3. Deploy
make cluster-deploy CONFIG_FILE_PATH=cluster-config.yaml
```

### Configuration File

Create a YAML config file with your cluster settings:

```yaml
# Required
ocp_version: "4.20"
pull_secret_path: ~/keys/pull-secret.json

# Optional
cluster_name: ocp
ctlplanes: 1
workers: 0
```

#### Required Fields

| Field | Description |
|-------|-------------|
| `ocp_version` | OpenShift version (e.g., `"4.20"` or `"4.20.6"`). If only major.minor, latest patch is used. |
| `pull_secret_path` | Path to Red Hat pull secret. Get it from https://console.redhat.com/openshift/install/pull-secret |

#### Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `cluster_name` | `ocp` | Name of the cluster |
| `domain` | `example.com` | Cluster domain |
| `ctlplanes` | `1` | Number of control plane nodes (1 = SNO) |
| `workers` | `0` | Number of worker nodes |
| `ctlplane.numcpus` | `6` | vCPUs per control plane |
| `ctlplane.memory` | `18432` | Memory (MB) per control plane |
| `worker.numcpus` | `4` | vCPUs per worker |
| `worker.memory` | `16384` | Memory (MB) per worker |
| `disk_size` | `120` | Disk size (GB) per node |
| `network` | `default` | Libvirt network name |
| `api_ip` | `192.168.122.253` | API VIP address |
| `pci_devices` | `[]` | PCI devices for GPU passthrough |
| `wait_timeout` | `3600` | Timeout (seconds) waiting for cluster ready |
| `no_wait` | `false` | Skip waiting for cluster ready |
| `version_channel` | `stable` | OCP release channel (stable, fast, candidate) |

### Local Deployment

Deploy on the local machine (requires libvirt/kcli installed):

```yaml
# cluster-config.yaml
ocp_version: "4.20"
pull_secret_path: ~/keys/pull-secret.json
cluster_name: my-cluster
```

```bash
make cluster-deploy CONFIG_FILE_PATH=cluster-config.yaml
```

### Remote Deployment

Deploy on a remote libvirt host via SSH:

```yaml
# cluster-config.yaml
ocp_version: "4.20"
pull_secret_path: ~/keys/pull-secret.json
cluster_name: my-cluster

remote:
  host: myserver.example.com
  user: root
  ssh_key_path: ~/.ssh/id_rsa
```

```bash
make cluster-deploy CONFIG_FILE_PATH=cluster-config.yaml
```

### GPU Passthrough

Pass PCI devices (GPUs) to cluster nodes:

```yaml
ocp_version: "4.20"
pull_secret_path: ~/keys/pull-secret.json

pci_devices:
  - "0000:b3:00.0"
  - "0000:b3:00.1"
```

### Multi-Node Cluster

Deploy HA cluster with multiple control planes and workers:

```yaml
ocp_version: "4.20"
pull_secret_path: ~/keys/pull-secret.json

ctlplanes: 3
workers: 2

ctlplane:
  numcpus: 8
  memory: 32768

worker:
  numcpus: 16
  memory: 65536
```

### Commands

```bash
# Deploy cluster
make cluster-deploy CONFIG_FILE_PATH=cluster-config.yaml

# Delete cluster
make cluster-delete CONFIG_FILE_PATH=cluster-config.yaml

# Dry run (show config without deploying)
make cluster-dry-run CONFIG_FILE_PATH=cluster-config.yaml

# Show help
make help
```

### Requirements

- Python 3.10+
- kcli installed (local or remote)
- libvirt configured
- Red Hat pull secret
