test:
	PYTHONPATH=. python3 -m unittest discover -s workflows/gpu_operator_versions/tests -v

# OpenShift cluster management
# Remote host in format: user@host or just host (defaults to root@host)
# If not set, deployment is done locally
REMOTE_HOST ?=
# OpenShift version to install (required for deploy)
# Example: OCP_CLUSTER_VERSION=4.20 or OCP_CLUSTER_VERSION=4.20.6
OCP_CLUSTER_VERSION ?=
# Path to pull secret file (required for deploy)
# Example: PULL_SECRET_PATH=/path/to/pull-secret.json
PULL_SECRET_PATH ?=
# Path to SSH private key file (optional, defaults to ~/.ssh/id_rsa etc.)
# Example: SSH_KEY_PATH=/path/to/id_rsa
SSH_KEY_PATH ?=
# PCI devices for passthrough (space-separated for multiple devices)
# Example: PCI_DEVICES=0000:b3:00.0 or PCI_DEVICES="0000:b3:00.0 0000:b3:00.1"
PCI_DEVICES ?=
# Number of control plane nodes (default: 1 for SNO)
# Example: CTLPLANES=3 for HA cluster
CTLPLANES ?=
# Number of worker nodes (default: 0 for SNO)
# Example: WORKERS=2
WORKERS ?=
# Number of vCPUs per control plane node (minimum 6 for KMM, AMD GPU Operator, NFD)
CTLPLANE_NUMCPUS ?=
# Number of vCPUs per worker node
WORKER_NUMCPUS ?=
# Cluster name (default: ocp)
# Example: CLUSTER_NAME=my-cluster
CLUSTER_NAME ?=
# Timeout for waiting for cluster ready (seconds)
WAIT_TIMEOUT ?= 3600

# Build remote args if REMOTE_HOST is set
ifdef REMOTE_HOST
  REMOTE_ARGS = --remote $(REMOTE_HOST)
else
  REMOTE_ARGS =
endif

# Build version args if OCP_CLUSTER_VERSION is set
ifdef OCP_CLUSTER_VERSION
  VERSION_ARGS = --version $(OCP_CLUSTER_VERSION)
else
  VERSION_ARGS =
endif

# Build pull secret args if PULL_SECRET_PATH is set
ifdef PULL_SECRET_PATH
  PULL_SECRET_ARGS = --pull-secret-path $(PULL_SECRET_PATH)
else
  PULL_SECRET_ARGS =
endif

# Build SSH key args if SSH_KEY_PATH is set
ifdef SSH_KEY_PATH
  SSH_KEY_ARGS = --ssh-key $(SSH_KEY_PATH)
else
  SSH_KEY_ARGS =
endif

# Build PCI device args if PCI_DEVICES is set
# Supports multiple devices: PCI_DEVICES="0000:b3:00.0 0000:b3:00.1"
ifdef PCI_DEVICES
  PCI_DEVICE_ARGS = $(foreach dev,$(PCI_DEVICES),--pci-device $(dev))
else
  PCI_DEVICE_ARGS =
endif

# Build node count args if set
ifdef CTLPLANES
  CTLPLANES_ARGS = --ctlplanes $(CTLPLANES)
else
  CTLPLANES_ARGS =
endif

ifdef WORKERS
  WORKERS_ARGS = --workers $(WORKERS)
else
  WORKERS_ARGS =
endif

# Build CPU args if set
ifdef CTLPLANE_NUMCPUS
  CTLPLANE_NUMCPUS_ARGS = --ctlplane-numcpus $(CTLPLANE_NUMCPUS)
else
  CTLPLANE_NUMCPUS_ARGS =
endif

ifdef WORKER_NUMCPUS
  WORKER_NUMCPUS_ARGS = --worker-numcpus $(WORKER_NUMCPUS)
else
  WORKER_NUMCPUS_ARGS =
endif

# Build cluster name args if set
ifdef CLUSTER_NAME
  CLUSTER_NAME_ARGS = --cluster-name $(CLUSTER_NAME)
else
  CLUSTER_NAME_ARGS =
endif

# ============================================
# OpenShift Cluster Management Targets
# ============================================

# Deploy OpenShift cluster (local or remote based on REMOTE_HOST)
# Default topology is SNO (1 control plane, 0 workers)
# Usage: make cluster-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json
# Usage: make cluster-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json REMOTE_HOST=root@myhost.example.com
# Usage: make cluster-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json CTLPLANES=3 WORKERS=2
cluster-deploy:
ifndef OCP_CLUSTER_VERSION
	$(error OCP_CLUSTER_VERSION is required. Usage: make cluster-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json)
endif
ifndef PULL_SECRET_PATH
	$(error PULL_SECRET_PATH is required. Usage: make cluster-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json)
endif
	python3 cluster-provision/main.py $(VERSION_ARGS) $(PULL_SECRET_ARGS) $(REMOTE_ARGS) $(SSH_KEY_ARGS) $(PCI_DEVICE_ARGS) $(CTLPLANES_ARGS) $(WORKERS_ARGS) $(CTLPLANE_NUMCPUS_ARGS) $(WORKER_NUMCPUS_ARGS) $(CLUSTER_NAME_ARGS) --wait-timeout $(WAIT_TIMEOUT) deploy

# Delete OpenShift cluster (local or remote based on REMOTE_HOST)
# Usage: make cluster-delete
# Usage: make cluster-delete REMOTE_HOST=root@myhost.example.com
cluster-delete:
	python3 cluster-provision/main.py $(REMOTE_ARGS) $(SSH_KEY_ARGS) $(CLUSTER_NAME_ARGS) delete

# Dry run deployment (local or remote based on REMOTE_HOST)
# Usage: make cluster-dry-run OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json
cluster-dry-run:
ifndef OCP_CLUSTER_VERSION
	$(error OCP_CLUSTER_VERSION is required. Usage: make cluster-dry-run OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json)
endif
ifndef PULL_SECRET_PATH
	$(error PULL_SECRET_PATH is required. Usage: make cluster-dry-run OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json)
endif
	python3 cluster-provision/main.py $(VERSION_ARGS) $(PULL_SECRET_ARGS) $(REMOTE_ARGS) $(SSH_KEY_ARGS) $(PCI_DEVICE_ARGS) $(CTLPLANES_ARGS) $(WORKERS_ARGS) $(CTLPLANE_NUMCPUS_ARGS) $(WORKER_NUMCPUS_ARGS) $(CLUSTER_NAME_ARGS) --dry-run deploy

# Help target
help:
	@echo "OpenShift Cluster Provisioner - Makefile targets"
	@echo ""
	@echo "Targets:"
	@echo "  make cluster-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json - Deploy cluster (default: SNO)"
	@echo "  make cluster-delete                                                                - Delete cluster"
	@echo "  make cluster-dry-run OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json - Dry run deployment"
	@echo ""
	@echo "Variables:"
	@echo "  OCP_CLUSTER_VERSION - OpenShift version to install (e.g., 4.20 or 4.20.6). REQUIRED for deploy."
	@echo "  PULL_SECRET_PATH    - Path to pull secret file. REQUIRED for deploy."
	@echo "  REMOTE_HOST         - Remote host in format user@host or host (default user: root)"
	@echo "  SSH_KEY_PATH        - Path to SSH private key file (optional, uses default keys if not set)"
	@echo "  PCI_DEVICES         - PCI devices for passthrough (e.g., '0000:b3:00.0' or '0000:b3:00.0 0000:b3:00.1')"
	@echo "  CTLPLANES           - Number of control plane nodes (default: 1 for SNO)"
	@echo "  WORKERS             - Number of worker nodes (default: 0 for SNO)"
	@echo "  CTLPLANE_NUMCPUS    - vCPUs per control plane node (min for KMM/GPU/NFD operators)"
	@echo "  WORKER_NUMCPUS      - vCPUs per worker node"
	@echo "  CLUSTER_NAME        - Name of the cluster (default: ocp)"
	@echo "  WAIT_TIMEOUT        - Timeout for cluster ready in seconds (default: 3600)"
	@echo ""
	@echo "Examples:"
	@echo "  # Deploy local SNO (Single Node OpenShift)"
	@echo "  make cluster-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=~/secret.json"
	@echo ""
	@echo "  # Deploy HA cluster (3 control planes + 2 workers)"
	@echo "  make cluster-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=~/secret.json CTLPLANES=3 WORKERS=2"

.PHONY: test cluster-deploy cluster-delete cluster-dry-run help
