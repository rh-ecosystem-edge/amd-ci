# AMD CI

Continuous Integration for AMD GPU Operator on OpenShift.

[CI Dashboard](https://rh-ecosystem-edge.github.io/amd-ci/)

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

# Show help
make help
```

### Requirements

- Python 3.10+
- kcli installed (local or remote)
- libvirt configured
- Red Hat pull secret

## GPU Tests

Once a cluster is up and the AMD GPU operator stack is installed
(`make cluster-operators`), two pytest suites verify the stack against the
hardware. Both live under `tests/amd_gpu/` and can run against a local cluster
(via `KUBECONFIG`) or a remote cluster (via `CONFIG_FILE_PATH`, which sets up an
SSH tunnel automatically).

### Basic Verification Tests

Fast, read-only checks that the operator stack is healthy: internal registry,
NFD labels, DeviceConfig, node labeller, device plugin, GPU resource reporting,
and ROCm tool validation (`rocm-smi` / `rocminfo`).

```bash
# Local cluster
make test-gpu KUBECONFIG=~/.kcli/clusters/<name>/auth/kubeconfig

# Remote cluster (SSH tunnel set up automatically)
make test-gpu CONFIG_FILE_PATH=cluster-config.yaml
```

### Stress / Workload Tests

Heavier tests that run real GPU workloads to confirm the stack can schedule and
execute compute on the hardware. They are **excluded from `make test-gpu`** and
must be run explicitly, as they pull very large images (`rocm/pytorch`,
`rocm/vllm`), sustain GPU load, and download a model:

- **PyTorch** — confirms the GPU is visible to PyTorch and computes a correct matmul.
- **GPU burn** — sustained matmul load on every visible GPU with a numerical
  correctness check to catch compute errors under load.
- **vLLM** — end-to-end LLM inference on a small model (`facebook/opt-125m`).

```bash
# Local cluster
make test-gpu-workload KUBECONFIG=~/.kcli/clusters/<name>/auth/kubeconfig

# Remote cluster (SSH tunnel set up automatically)
make test-gpu-workload CONFIG_FILE_PATH=cluster-config.yaml
```

> **Note:** the cluster must be able to pull the `rocm/*` images, and the vLLM
> test additionally needs network access to download the model. First runs can
> be slow due to the large image pulls (hence a 30-minute default pod timeout).

#### Running an Individual Workload Test

Each workload test also has its own target, so you can run just one instead of
the whole suite. All accept the same `KUBECONFIG` / `CONFIG_FILE_PATH` options
and environment variables as `make test-gpu-workload`:

```bash
# PyTorch only
make test-gpu-pytorch KUBECONFIG=~/.kcli/clusters/<name>/auth/kubeconfig
make test-gpu-pytorch CONFIG_FILE_PATH=cluster-config.yaml

# GPU burn only
make test-gpu-burn KUBECONFIG=~/.kcli/clusters/<name>/auth/kubeconfig
make test-gpu-burn CONFIG_FILE_PATH=cluster-config.yaml

# vLLM only
make test-gpu-vllm KUBECONFIG=~/.kcli/clusters/<name>/auth/kubeconfig
make test-gpu-vllm CONFIG_FILE_PATH=cluster-config.yaml
```

| Target | Marker | Test |
|--------|--------|------|
| `make test-gpu-pytorch` | `pytorch` | PyTorch matmul |
| `make test-gpu-burn` | `gpu_burn` | Sustained GPU burn |
| `make test-gpu-vllm` | `vllm` | vLLM inference |

#### Configuration

All workload tests are configurable via environment variables (sensible defaults
are used if unset):

| Variable | Default | Description |
|----------|---------|-------------|
| `AMD_PYTORCH_TEST_IMAGE` | `rocm/pytorch:latest` | Image for the PyTorch and GPU burn tests |
| `AMD_VLLM_TEST_IMAGE` | `rocm/vllm:latest` | Image for the vLLM test |
| `AMD_VLLM_MODEL` | `facebook/opt-125m` | Model loaded by the vLLM test |
| `AMD_VLLM_TEST_ENABLED` | `true` | Set to `false` to skip the vLLM test (e.g. disconnected clusters) |
| `AMD_WORKLOAD_GPU_COUNT` | `1` | Number of GPUs each workload pod requests |
| `AMD_GPU_BURN_DURATION` | `60` | Seconds the GPU burn test sustains load |
| `AMD_WORKLOAD_POD_TIMEOUT` | `1800` | Seconds to wait for a workload pod to finish |

### Running Tests Directly with pytest

Both suites can also be invoked directly (useful for a single test or extra
flags). The `workload` marker separates the two:

```bash
export KUBECONFIG=~/.kcli/clusters/<name>/auth/kubeconfig

# Basic suite only (default)
PYTHONPATH=. python3 -m pytest tests/amd_gpu/ -v -m "not workload"

# Workload suite only
PYTHONPATH=. python3 -m pytest tests/amd_gpu/ -v -m "workload"

# A single workload test, by marker
PYTHONPATH=. python3 -m pytest tests/amd_gpu/ -v -m "pytorch"
PYTHONPATH=. python3 -m pytest tests/amd_gpu/ -v -m "gpu_burn"
PYTHONPATH=. python3 -m pytest tests/amd_gpu/ -v -m "vllm"

# A single workload test, by node ID
PYTHONPATH=. python3 -m pytest tests/amd_gpu/test_amd_gpu_workload.py::TestVLLM::test_vllm_inference -v
```
