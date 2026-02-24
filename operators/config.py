"""
Configure NFD instance, NFD feature rule, amdgpu blacklist, DeviceConfig,
and cluster monitoring per AMD OpenShift OLM doc.

Reference:
  https://instinct.docs.amd.com/projects/gpu-operator/en/latest/installation/openshift-olm.html
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from operators.errors import OperatorError

from operators.constants import (
    AMD_GPU_LABEL,
    AMDGPU_BLACKLIST_CONTENTS_B64,
    DEFAULT_DRIVER_IMAGE,
    DEFAULT_DRIVER_VERSION,
    DEVICECONFIG_NAME,
    MACHINECONFIG_AMDGPU_BLACKLIST,
    NAMESPACE_AMD_GPU,
    NAMESPACE_NFD,
    NFD_FEATURE_RULE_NAME,
    NFD_INSTANCE_NAME,
)

if TYPE_CHECKING:
    from operators.oc_runner import OcRunner


def create_nfd_instance(oc: OcRunner, ocp_version: str | None = None) -> None:
    """Create a minimal NodeFeatureDiscovery instance to start NFD pods.

    AMD GPU detection rules are deployed separately via
    :func:`create_nfd_feature_rule`.  For OpenShift 4.16, the operand
    image must be specified explicitly; 4.17+ auto-selects it.
    """
    print("Creating NodeFeatureDiscovery instance...")
    operand_block = ""
    if ocp_version and ocp_version.startswith("4.16"):
        operand_block = """
  operand:
    image: quay.io/openshift/origin-node-feature-discovery:latest
    imagePullPolicy: IfNotPresent
    servicePort: 12000
"""
    yaml = f"""apiVersion: nfd.openshift.io/v1
kind: NodeFeatureDiscovery
metadata:
  name: {NFD_INSTANCE_NAME}
  namespace: {NAMESPACE_NFD}
spec:{operand_block}
  workerConfig:
    configData: ""
"""
    oc.apply_yaml(yaml)
    print("  NodeFeatureDiscovery instance created.")


# NOTE: the YAML strings below use double-braces where needed to survive
# Python f-string interpolation *and* to produce valid NFD YAML.  The
# {op: In, value: [...]} blocks are inside a regular (non-f) string that
# is concatenated via the top-level f-string.
NFD_FEATURE_RULE_YAML = """\
apiVersion: nfd.openshift.io/v1alpha1
kind: NodeFeatureRule
metadata:
  name: {name}
  namespace: {namespace}
spec:
  rules:
    - name: amd-gpu
      labels:
        feature.node.kubernetes.io/amd-gpu: "true"
      matchAny:
        - matchFeatures:
            - feature: pci.device
              matchExpressions:
                vendor: {{op: In, value: ["1002"]}}
                device: {{op: In, value: [
                  "75a3",
                  "75a0",
                  "74a5",
                  "74a2",
                  "74a8",
                  "74a0",
                  "74a1",
                  "74a9",
                  "740f",
                  "7408",
                  "740c",
                  "738c",
                  "738e"
                ]}}
    - name: amd-vgpu
      labels:
        feature.node.kubernetes.io/amd-vgpu: "true"
      matchAny:
        - matchFeatures:
            - feature: pci.device
              matchExpressions:
                vendor: {{op: In, value: ["1002"]}}
                device: {{op: In, value: [
                  "75b3",
                  "75b0",
                  "74b9",
                  "74b6",
                  "74bc",
                  "74b5",
                  "74bd",
                  "7410"
                ]}}
"""


def create_nfd_feature_rule(oc: OcRunner) -> None:
    """Create a NodeFeatureRule CR for AMD GPU / vGPU PCI detection.

    Per the AMD docs, a separate ``NodeFeatureRule`` is the recommended
    approach when the ``NodeFeatureDiscovery`` is already deployed.  The
    rule matches PCI vendor ``1002`` (AMD) against all supported GPU and
    vGPU device IDs and applies the ``feature.node.kubernetes.io/amd-gpu``
    and ``feature.node.kubernetes.io/amd-vgpu`` labels.
    """
    print("Creating NodeFeatureRule for AMD GPU detection...")
    yaml = NFD_FEATURE_RULE_YAML.format(
        name=NFD_FEATURE_RULE_NAME,
        namespace=NAMESPACE_AMD_GPU,
    )
    oc.apply_yaml(yaml)
    print("  NodeFeatureRule created.")


def create_amdgpu_blacklist(oc: OcRunner, role: str = "worker") -> None:
    """
    Create MachineConfig to blacklist amdgpu kernel module (for out-of-tree driver).
    Doc: use role 'master' for Single Node OpenShift, 'worker' otherwise.
    WARNING: MachineConfigOperator will reboot selected nodes.
    """
    print(f"Creating MachineConfig to blacklist amdgpu (role={role})...")
    yaml = f"""apiVersion: machineconfiguration.openshift.io/v1
kind: MachineConfig
metadata:
  labels:
    machineconfiguration.openshift.io/role: {role}
  name: {MACHINECONFIG_AMDGPU_BLACKLIST}
spec:
  config:
    ignition:
      version: 3.2.0
    storage:
      files:
        - path: "/etc/modprobe.d/amdgpu-blacklist.conf"
          mode: 420
          overwrite: true
          contents:
            source: "data:text/plain;base64,{AMDGPU_BLACKLIST_CONTENTS_B64}"
"""
    oc.apply_yaml(yaml)
    print("  amdgpu blacklist MachineConfig created (nodes may reboot).")


def create_device_config(
    oc: OcRunner,
    driver_version: str = DEFAULT_DRIVER_VERSION,
    driver_image: str | None = None,
    enable_metrics: bool = True,
    api_version: str = "amd.com/v1alpha1",
) -> None:
    """
    Create DeviceConfig CR to trigger GPU driver installation and optional metrics.
    api_version should match the AMD GPU Operator CSV (e.g. from wait_for_device_config_crd).
    """
    print("Creating DeviceConfig...")
    image = driver_image or DEFAULT_DRIVER_IMAGE
    metrics_block = ""
    if enable_metrics:
        metrics_block = """
  metricsExporter:
    enable: true
    prometheus:
      serviceMonitor:
        enable: true
        interval: "60s"
        attachMetadata:
          node: true
"""
    yaml = f"""apiVersion: {api_version}
kind: DeviceConfig
metadata:
  name: {DEVICECONFIG_NAME}
  namespace: {NAMESPACE_AMD_GPU}
spec:
  driver:
    enable: true
    image: {image}
    version: {driver_version}
  devicePlugin:
    enableNodeLabeller: true
  selector:
    {AMD_GPU_LABEL}: "true"
{metrics_block}
"""
    oc.apply_yaml(yaml)
    print("  DeviceConfig created.")


def enable_cluster_monitoring(oc: OcRunner) -> None:
    """Label openshift-amd-gpu namespace for OpenShift cluster monitoring."""
    print("Enabling cluster monitoring for AMD GPU Operator namespace...")
    r = oc.oc(
        "label",
        "namespace",
        NAMESPACE_AMD_GPU,
        "openshift.io/cluster-monitoring=true",
        "--overwrite",
        timeout=30,
    )
    if r.returncode != 0:
        raise OperatorError(
            f"Failed to label namespace for monitoring: {r.stderr or r.stdout}"
        )
    print("  Cluster monitoring label applied.")
