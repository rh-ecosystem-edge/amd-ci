test:
	PYTHONPATH=. python3 -m unittest discover -s workflows/gpu_operator_versions/tests -v

# Run AMD GPU operator verification tests (cluster must be ready with operators installed)
# For local clusters:  make test-gpu KUBECONFIG=~/.kcli/clusters/<name>/auth/kubeconfig
# For remote clusters: make test-gpu CONFIG_FILE_PATH=cluster-config.yaml  (sets up SSH tunnel automatically)
# Optional env vars: AMD_DEVICECONFIG_NAME, AMD_GPU_NAMESPACE
test-gpu:
ifdef CONFIG_FILE_PATH
	python3 cluster-provision/main.py --config $(CONFIG_FILE_PATH) test-gpu
else
	PYTHONPATH=. python3 -m pytest tests/amd_gpu/ -v
endif

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

# Deploy OpenShift cluster (cluster only — does NOT install operators or run tests)
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

# Install AMD GPU Operator and dependencies (operators only — does NOT run tests)
# Cluster must already exist (use after cluster-deploy)
# Usage: make cluster-operators CONFIG_FILE_PATH=cluster-config.yaml
cluster-operators:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required. Usage: make cluster-operators CONFIG_FILE_PATH=cluster-config.yaml)
endif
	python3 cluster-provision/main.py --config $(CONFIG_FILE_PATH) operators

# Clean up AMD GPU Operator stack (reverse of operator install)
# Usage: make cluster-cleanup CONFIG_FILE_PATH=cluster-config.yaml
cluster-cleanup:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required. Usage: make cluster-cleanup CONFIG_FILE_PATH=cluster-config.yaml)
endif
	python3 cluster-provision/main.py --config $(CONFIG_FILE_PATH) cleanup

# Help target
help:
	@echo "OpenShift Cluster Provisioner"
	@echo ""
	@echo "Setup:"
	@echo "  1. Copy cluster-config.yaml.example to cluster-config.yaml"
	@echo "  2. Edit cluster-config.yaml with your settings"
	@echo ""
	@echo "Each target is responsible for a single task and does NOT trigger the next one."
	@echo "Typical workflow:"
	@echo "  make cluster-deploy   -> make cluster-operators -> make test-gpu"
	@echo ""
	@echo "Targets:"
	@echo "  make cluster-deploy CONFIG_FILE_PATH=<path>    - Deploy cluster (no operators, no tests)"
	@echo "  make cluster-operators CONFIG_FILE_PATH=<path> - Install AMD GPU operators (no tests)"
	@echo "  make test-gpu CONFIG_FILE_PATH=<path>          - Run AMD GPU verification tests only"
	@echo "  make test-gpu                                  - Run AMD GPU tests (local kubeconfig)"
	@echo "  make cluster-cleanup CONFIG_FILE_PATH=<path>   - Clean up AMD GPU operator stack"
	@echo "  make cluster-delete CONFIG_FILE_PATH=<path>    - Delete cluster"
	@echo "  make help                                      - Show this help"
	@echo ""
	@echo "Config file options (see cluster-config.yaml.example):"
	@echo "  ocp_version, pull_secret_path, cluster_name, ctlplanes, workers,"
	@echo "  ctlplane.numcpus, worker.numcpus, remote.host, remote.user,"
	@echo "  remote.ssh_key_path, pci_devices, wait_timeout,"
	@echo "  operators.install, operators.machine_config_role, operators.driver_version"

.PHONY: test test-gpu cluster-deploy cluster-delete cluster-operators cluster-cleanup help
