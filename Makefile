test:
	PYTHONPATH=. python3 -m unittest discover -s workflows/gpu_operator_versions/tests -v

# ============================================
# OpenShift Cluster Provisioner
# ============================================
#
# All configuration is provided via a YAML config file.
# See cluster-config.yaml.example for all available options.

# Path to YAML configuration file (required for deploy)
CONFIG_FILE_PATH ?=

# ============================================
# OpenShift Cluster Management Targets
# ============================================

# Deploy OpenShift cluster
# Usage: make cluster-deploy CONFIG_FILE_PATH=cluster-config.yaml
cluster-deploy:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required. Usage: make cluster-deploy CONFIG_FILE_PATH=cluster-config.yaml)
endif
	python3 cluster-provision/main.py --config $(CONFIG_FILE_PATH) deploy

# Delete OpenShift cluster
# Usage: make cluster-delete CONFIG_FILE_PATH=cluster-config.yaml
cluster-delete:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required. Usage: make cluster-delete CONFIG_FILE_PATH=cluster-config.yaml)
endif
	python3 cluster-provision/main.py --config $(CONFIG_FILE_PATH) delete

# Help target
help:
	@echo "OpenShift Cluster Provisioner"
	@echo ""
	@echo "Setup:"
	@echo "  1. Copy cluster-config.yaml.example to cluster-config.yaml"
	@echo "  2. Edit cluster-config.yaml with your settings"
	@echo "  3. Run: make cluster-deploy CONFIG_FILE_PATH=cluster-config.yaml"
	@echo ""
	@echo "Targets:"
	@echo "  make cluster-deploy CONFIG_FILE_PATH=<path>  - Deploy cluster"
	@echo "  make cluster-delete CONFIG_FILE_PATH=<path>  - Delete cluster"
	@echo "  make help                               - Show this help"
	@echo ""
	@echo "Config file options (see cluster-config.yaml.example):"
	@echo "  ocp_version, pull_secret_path, cluster_name, ctlplanes, workers,"
	@echo "  ctlplane.numcpus, worker.numcpus, remote.host, remote.user,"
	@echo "  remote.ssh_key_path, pci_devices, wait_timeout"

.PHONY: test cluster-deploy cluster-delete help
