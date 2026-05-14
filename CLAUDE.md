# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CI infrastructure for testing the AMD GPU Operator on OpenShift clusters. The system provisions OpenShift clusters (via kcli/libvirt), installs the AMD GPU operator stack (NFD, KMM, AMD GPU Operator) through OLM, runs GPU verification tests, and automatically tracks new versions. A CI dashboard is published to GitHub Pages.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Common Commands

```bash
# Run workflow version tests (unit tests, no cluster needed)
make test

# Run AMD GPU verification tests (requires a running cluster with operators installed)
make test-gpu KUBECONFIG=~/.kcli/clusters/<name>/auth/kubeconfig   # local cluster
make test-gpu CONFIG_FILE_PATH=cluster-config.yaml                  # remote cluster (SSH tunnel)

# Run a single test file or test case
PYTHONPATH=. python3 -m pytest tests/amd_gpu/test_amd_gpu_basic.py -v
PYTHONPATH=. python3 -m pytest tests/amd_gpu/test_amd_gpu_basic.py::TestClassName::test_name -v

# Cluster lifecycle (all require CONFIG_FILE_PATH=cluster-config.yaml)
# See cluster-config.yaml.example for all available options
make cluster-deploy    # deploy cluster only (no operators, no tests)
make cluster-operators # install AMD GPU operator stack
make cluster-cleanup   # remove operator stack
make cluster-delete    # destroy cluster
make must-gather       # collect diagnostics
```

## Directory Structure

- **`cluster-provision/`** — Cluster lifecycle orchestrator. `main.py` is the single entrypoint for all `make` targets; it parses the YAML config file and dispatches to the requested subcommand (deploy, delete, operators, test-gpu, cleanup, must-gather). Other modules: `deploy.py`/`delete.py` for cluster lifecycle, `config.py` for YAML parsing, `params.py` for kcli parameter building, `remote.py` for remote libvirt host operations, `kcli_preflight.py` for pre-deploy validation, `must_gather.py` for diagnostic collection (local and remote).

- **`operators/`** — OLM-based operator installation and cleanup. `main.py` orchestrates a strict 14-step installation sequence (see "Operator Installation" below). `install.py` handles OLM subscription creation and CSV readiness. `config.py` generates Kubernetes YAML for MachineConfigs, NFD instances, DeviceConfigs, etc. `prerequisites.py` verifies required cluster operators and configures the internal registry. `cleanup.py` reverses the installation. `version_resolver.py` resolves `X.Y` to `X.Y.Z` patch versions. `oc_runner.py` is a re-export shim that imports from `shared.oc_runner` for backward compatibility.

- **`shared/`** — Cross-cutting utilities used by cluster-provision, operators, and tests. `oc_runner.py` defines the `OcRunner` interface with two implementations: `LocalOcRunner` (runs `oc` with a local kubeconfig) and `RemoteOcRunner` (runs `oc` on a remote host via SSH). `ssh.py` provides SSH/SCP helpers with multiplexing. `version_utils.py` and `amd_gpu_releases.py` handle version resolution.

- **`tests/amd_gpu/`** — pytest-based GPU verification tests that use the Kubernetes Python SDK directly. `conftest.py` provides session-scoped fixtures for K8s API clients and AMD GPU node discovery. `helpers.py` has utilities for spawning GPU workload pods. `runner.py` wraps pytest invocation for both local and remote (SSH-tunneled) execution.

- **`workflows/gpu_operator_versions/`** — Automated version detection. `update_versions.py` is the main script (run by a daily GitHub Action). Fetches latest OCP and GPU operator versions from upstream, diffs against `versions.json`, generates Prow test commands for changed combinations, and updates the version file. Has its own test suite under `tests/`.

- **`workflows/gpu_operator_dashboard/`** — CI dashboard generator. `fetch_ci_data.py` pulls test results from GCS (Prow artifacts) for closed PRs, resolves exact OCP and GPU operator versions from build artifacts, and merges results. `generate_ci_dashboard.py` renders an HTML dashboard by loading and concatenating HTML templates from `templates/`.

- **`workflows/common/`** — Shared utilities for workflow scripts. `utils.py` provides logging and general helpers. `templates.py` provides HTML template loading used by the dashboard generator.

- **`scripts/`** — Shell utilities. `must-gather.sh` collects diagnostic data (pod logs, CRDs, node info) for NFD, AMD GPU Operator, and KMM.

## Flows

### 1. Version Detection Workflow (`update-versions.yaml` → `update_versions.py`)

Runs daily (Mon–Fri) via GitHub Actions. Fetches the latest OCP versions (from Red Hat release API) and AMD GPU Operator versions (from the operator catalog). Compares against the stored `versions.json` and computes diffs. If versions changed, it generates a test matrix: new OCP versions are tested against GPU operator releases, new GPU operator versions are tested against all OCP versions. Outputs Prow `/test` commands to a file. The GitHub Action then commits the updated `versions.json` and opens a PR whose description contains the test trigger commands.

### 2. Dashboard Generation Workflow (`generate_matrix_page.yaml`)

Runs on PR merge and manual dispatch. `fetch_ci_data.py` queries GCS for Prow `finished.json` artifacts from closed PRs, extracts test status (SUCCESS/FAILURE), and resolves exact OCP and GPU operator versions from build artifacts (`ocp.version` file and install-operators build log). Results are grouped by OCP version, deduplicated per (OCP, GPU) combination (preferring SUCCESS, then latest timestamp), and merged with existing baseline data. `generate_ci_dashboard.py` then renders an HTML matrix page and deploys it to GitHub Pages.

### 3. Cluster Deployment (`make cluster-deploy`)

Dispatches to `cluster-provision/deploy.py`. **Local mode**: runs preflight checks (kcli installed, pull secret exists, kcli config valid), then invokes `kcli create cluster openshift` with parameters built from the YAML config. **Remote mode** (when `remote.host` is set): sets up the remote libvirt host, configures a kcli remote client, cleans up any existing cluster, launches `kcli create cluster` in the background, waits for VMs to appear, pushes SSH keys to the remote host, shuts down VMs to apply a container storage fix (composefs/overlay workaround), optionally attaches PCI devices for GPU passthrough, restarts VMs, sets up cluster access (kubeconfig fetch, /etc/hosts, SSH tunnel), and waits for the cluster API to become ready.

### 4. Operator Installation (`make cluster-operators`)

Dispatches to `operators/main.py:install_operators()`. Follows a strict ordering that matches AMD's OpenShift docs and eco-gotests:
1. Verify prerequisite operators (Service CA, OLM, MCO, Image Registry)
2. Configure internal image registry (set storage, switch to Managed)
3. Wait for cluster stability (all nodes Ready, all ClusterOperators healthy)
4. Create `amdgpu` blacklist MachineConfig — done **before** operators so the MCO node reboot doesn't disrupt operator pods
5. Wait for MachineConfigPool to finish updating (handles node reboot, including API downtime on SNO)
6. Wait for cluster stability after reboot
7. Install NFD, KMM, and AMD GPU Operator via OLM subscriptions (waits for each CSV to reach Succeeded)
8. Create NodeFeatureDiscovery instance
9. Create NodeFeatureRule for AMD GPU detection
10. Wait for NFD to label nodes
11. Wait for DeviceConfig CRD to appear, then create DeviceConfig
12. Enable cluster monitoring (user-workload)
13. Wait for cluster stability
14. Wait for GPU resources (`amd.com/gpu` capacity on nodes, device-plugin pods running)

### 5. Cluster Deletion (`make cluster-delete`)

Dispatches to `cluster-provision/delete.py`. **Local**: runs `kcli delete cluster <name> --yes` and removes `~/.kcli/clusters/<name>`. **Remote**: checks SSH connectivity, gets/creates the kcli remote client, runs the delete command against the remote host, and cleans up local artifacts.

### 6. GPU Verification Tests (`make test-gpu`)

Runs the pytest suite in `tests/amd_gpu/test_amd_gpu_basic.py`. Tests verify the full AMD GPU operator stack is working end-to-end:
- **Internal registry**: at least one image-registry pod is Running and healthy
- **NFD labels**: all worker nodes carry `feature.node.kubernetes.io/amd-gpu=true`
- **DeviceConfig**: the CR exists in the `openshift-amd-gpu` namespace
- **Node labeller**: pods are Running and GPU metadata labels (device-id, vendor, family) are present on all GPU nodes with known AMD device IDs
- **Device plugin**: one pod per GPU node, all Running; every GPU node reports `amd.com/gpu >= 1` in capacity and allocatable
- **ROCm validation**: spawns a GPU workload pod and runs `rocm-smi` (detects GPUs) and `rocminfo` (validates GPU architecture, agent info, AMD vendor string)

## Key Patterns

- The main entrypoint is always `cluster-provision/main.py --config <yaml> <command>`. Make targets are thin wrappers.
- No `setup.py`/`pyproject.toml` — use `PYTHONPATH=.` or `sys.path` manipulation for cross-package imports.
- `OcRunner.oc()` returns `subprocess.CompletedProcess` — callers check `returncode` rather than catching exceptions.
- `OcRunner.apply_yaml()` accepts YAML strings, not file paths. For remote, it SCPs a temp file.
- All cluster operations support local and remote modes, controlled by the `remote` section in config. Remote mode uses SSH multiplexing (`shared/ssh.py`).
- `versions.json` tracks detected versions under two keys: `gpu-operator` (minor → latest patch) and `ocp` (minor → latest patch).
- Prow job definitions live in the openshift/release repo: `ci-operator/config/rh-ecosystem-edge/amd-ci/`.
