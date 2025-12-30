test:
	PYTHONPATH=. python3 -m unittest discover -s workflows/gpu_operator_versions/tests -v

# SNO cluster management
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

# ============================================
# SNO Cluster Management Targets
# ============================================

# Deploy SNO cluster (local or remote based on REMOTE_HOST)
# Usage: make sno-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json
# Usage: make sno-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json REMOTE_HOST=root@myhost.example.com
sno-deploy:
ifndef OCP_CLUSTER_VERSION
	$(error OCP_CLUSTER_VERSION is required. Usage: make sno-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json)
endif
ifndef PULL_SECRET_PATH
	$(error PULL_SECRET_PATH is required. Usage: make sno-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json)
endif
	python3 sno-deployer/main.py $(VERSION_ARGS) $(PULL_SECRET_ARGS) $(REMOTE_ARGS) $(SSH_KEY_ARGS) $(PCI_DEVICE_ARGS) --wait-timeout $(WAIT_TIMEOUT) deploy

# Delete SNO cluster (local or remote based on REMOTE_HOST)
# Usage: make sno-delete
# Usage: make sno-delete REMOTE_HOST=root@myhost.example.com
sno-delete:
	python3 sno-deployer/main.py $(REMOTE_ARGS) $(SSH_KEY_ARGS) delete

# Dry run deployment (local or remote based on REMOTE_HOST)
# Usage: make sno-dry-run OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json
sno-dry-run:
ifndef OCP_CLUSTER_VERSION
	$(error OCP_CLUSTER_VERSION is required. Usage: make sno-dry-run OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json)
endif
ifndef PULL_SECRET_PATH
	$(error PULL_SECRET_PATH is required. Usage: make sno-dry-run OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json)
endif
	python3 sno-deployer/main.py $(VERSION_ARGS) $(PULL_SECRET_ARGS) $(REMOTE_ARGS) $(SSH_KEY_ARGS) $(PCI_DEVICE_ARGS) --dry-run deploy

# Help target
help:
	@echo "SNO Deployer - Makefile targets"
	@echo ""
	@echo "Targets:"
	@echo "  make sno-deploy OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json - Deploy SNO cluster"
	@echo "  make sno-delete                                                               - Delete SNO cluster"
	@echo "  make sno-dry-run OCP_CLUSTER_VERSION=4.20 PULL_SECRET_PATH=/path/to/secret.json - Dry run deployment"
	@echo ""
	@echo "Variables:"
	@echo "  OCP_CLUSTER_VERSION - OpenShift version to install (e.g., 4.20 or 4.20.6). REQUIRED for deploy."
	@echo "  PULL_SECRET_PATH    - Path to pull secret file. REQUIRED for deploy."
	@echo "  REMOTE_HOST         - Remote host in format user@host or host (default user: root)"
	@echo "  SSH_KEY_PATH        - Path to SSH private key file (optional, uses default keys if not set)"
	@echo "  PCI_DEVICES         - PCI devices for passthrough (e.g., '0000:b3:00.0' or '0000:b3:00.0 0000:b3:00.1')"
	@echo "  WAIT_TIMEOUT        - Timeout for cluster ready in seconds (default: 3600)"

.PHONY: test sno-deploy sno-delete sno-dry-run help
