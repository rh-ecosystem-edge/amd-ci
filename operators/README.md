# AMD GPU Operator and Dependencies (OLM)

This package installs and configures the AMD GPU Operator on OpenShift after cluster install, following the [OpenShift (OLM) installation guide](https://instinct.docs.amd.com/projects/gpu-operator/en/release-v1.4.1/installation/openshift-olm.html).

## Steps performed (in order)

1. **Prerequisites** – Verify Service CA, OLM, MachineConfig, and Cluster Image Registry operators are running.
2. **Internal registry** – Configure storage (emptyDir) and set managementState to Managed.
3. **Install operators** – NFD (Node Feature Discovery), KMM (Kernel Module Management), AMD GPU Operator via OLM subscriptions.
4. **NFD rule** – Create NodeFeatureDiscovery instance with AMD GPU PCI device labels.
5. **amdgpu blacklist** – MachineConfig to blacklist the in-tree amdgpu module (nodes may reboot).
6. **DeviceConfig** – Create CR to enable driver and optional metrics exporter.
7. **Cluster monitoring** – Label `openshift-amd-gpu` namespace for OpenShift monitoring.

## Usage

Enable in your cluster config YAML:

```yaml
operators:
  install: true
  machine_config_role: worker   # use "master" for SNO
  driver_version: "30.20.1"
  enable_metrics: true
```

Then deploy as usual; operators are installed automatically after the cluster is ready.

## Run only the operator phase

If the cluster already exists and you want to run or re-run only the operator installation (e.g. after a fix or to retry):

```bash
make cluster-operators CONFIG_FILE_PATH=cluster-config.yaml
```

Or:

```bash
PYTHONPATH=. python3 cluster-provision/main.py --config cluster-config.yaml operators
```

Uses the same config file (cluster name, operators.*, remote vs local) and the existing cluster’s kubeconfig.

## Retrying after a failed operator (e.g. KMM)

- **You do not need to clean NFD.** Re-running the operator phase will re-apply the NFD subscription (no change); `_wait_for_csv` will see NFD’s CSV already **Succeeded** and continue.
- **Clean only the failed operator.** For example, after fixing the KMM package name in `constants.py`, clean only KMM so a new Subscription is created with the correct package:

  ```bash
  export KUBECONFIG=~/.kcli/clusters/ocp/auth/kubeconfig   # or your cluster’s kubeconfig
  oc delete subscription kmm -n openshift-kmm --ignore-not-found
  oc delete operatorgroup openshift-kmm -n openshift-kmm --ignore-not-found
  oc delete csv -n openshift-kmm --all --ignore-not-found
  ```

  Then run:

  ```bash
  make cluster-operators CONFIG_FILE_PATH=cluster-config.yaml
  ```

  NFD will be re-applied and will finish immediately (already installed); KMM and AMD GPU Operator will install with the updated settings.

## Running without AMD GPU hardware

You can run the operator phase on a cluster that has no AMD GPUs (e.g. local dev). The operators and their CRDs install regardless of hardware. The script waits for the DeviceConfig CRD (`deviceconfigs.amd.com`) after the AMD GPU Operator CSV is Succeeded; that CRD is provided by the operator, not by the presence of GPUs. If the DeviceConfig CRD wait times out, the error message will list any AMD-related CRDs present in the cluster—if the certified operator uses a different CRD name, set `operators.constants.DEVICECONFIG_CRD_NAME` to that name.

## Layout

- `oc_runner.py` – Re-exports OcRunner classes from `shared/oc_runner.py` for backward compatibility.
- `prerequisites.py` – Verify required operators and configure internal registry.
- `install.py` – OLM subscriptions for NFD, KMM, AMD GPU Operator.
- `config.py` – NFD rule, MachineConfig blacklist, DeviceConfig, monitoring label.
- `main.py` – Orchestrator and `OperatorInstallConfig`.
- `constants.py` – Namespaces, package names, default values.
