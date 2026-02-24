"""
Clean up AMD GPU Operator stack using oc commands.

Reverse of install_operators(): removes DeviceConfig, FeatureRule, operators,
MachineConfig, node labels, image registry config, and namespaces.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from operators.constants import (
    DEVICECONFIG_NAME,
    MACHINECONFIG_AMDGPU_BLACKLIST,
    NAMESPACE_AMD_GPU,
    NAMESPACE_KMM,
    NAMESPACE_NFD,
    NFD_FEATURE_RULE_NAME,
    NFD_INSTANCE_NAME,
)

if TYPE_CHECKING:
    from operators.oc_runner import OcRunner


def oc_delete_quiet(oc: OcRunner, *args: str) -> None:
    """Run ``oc delete`` and ignore 'not found' errors."""
    r = oc.oc("delete", *args, "--ignore-not-found", timeout=60)
    if r.returncode != 0 and "not found" not in (r.stderr or "").lower():
        print(f"  Warning: oc delete {' '.join(args)}: {r.stderr or r.stdout}")


def uninstall_operator(oc: OcRunner, namespace: str, subscription_name: str) -> None:
    """Uninstall an OLM operator by deleting Subscription, CSV, and OperatorGroup."""
    # Get installed CSV from subscription.
    r = oc.oc(
        "get", "subscription", subscription_name, "-n", namespace,
        "-o", "jsonpath={.status.installedCSV}",
        timeout=15,
    )
    csv_name = (r.stdout or "").strip() if r.returncode == 0 else ""

    oc_delete_quiet(oc, "subscription", subscription_name, "-n", namespace)

    if csv_name:
        oc_delete_quiet(oc, "csv", csv_name, "-n", namespace)

    # Delete all operator groups in the namespace.
    r = oc.oc(
        "get", "operatorgroup", "-n", namespace,
        "-o", "jsonpath={.items[*].metadata.name}",
        timeout=15,
    )
    if r.returncode == 0 and r.stdout:
        for og in r.stdout.strip().split():
            oc_delete_quiet(oc, "operatorgroup", og, "-n", namespace)


def remove_gpu_node_labels(oc: OcRunner) -> None:
    """Remove AMD GPU labels from all nodes."""
    labels_to_remove = [
        "amd.com/gpu",
        "amd.com/gpu.cu-count",
        "amd.com/gpu.device-id",
        "amd.com/gpu.driver-version",
        "amd.com/gpu.family",
        "amd.com/gpu.simd-count",
        "amd.com/gpu.vram",
        "beta.amd.com/gpu.cu-count",
        "beta.amd.com/gpu.device-id",
        "beta.amd.com/gpu.family",
        "beta.amd.com/gpu.simd-count",
        "beta.amd.com/gpu.vram",
        "feature.node.kubernetes.io/amd-gpu",
        "feature.node.kubernetes.io/amd-vgpu",
    ]
    r = oc.oc(
        "get", "nodes", "-o", "jsonpath={.items[*].metadata.name}",
        timeout=15,
    )
    if r.returncode != 0 or not r.stdout:
        return
    for node_name in r.stdout.strip().split():
        label_args = [f"{lbl}-" for lbl in labels_to_remove]
        oc.oc("label", "node", node_name, *label_args, "--overwrite", timeout=15)


def cleanup_operators(oc: OcRunner) -> None:
    """Full cleanup of the AMD GPU operator stack in reverse order.

    1. Delete DeviceConfig
    2. Delete NodeFeatureRule
    3. Delete NodeFeatureDiscovery
    4. Uninstall AMD GPU Operator
    5. Uninstall KMM Operator
    6. Uninstall NFD Operator
    7. Delete MachineConfig (amdgpu blacklist)
    8. Remove AMD GPU node labels
    9. Reset image registry to Removed
    10. Delete operator namespaces
    """
    print("\n" + "=" * 60)
    print("AMD GPU Operator Stack Cleanup")
    print("=" * 60)

    print("Deleting DeviceConfig...")
    oc_delete_quiet(
        oc, "deviceconfigs.amd.com", DEVICECONFIG_NAME,
        "-n", NAMESPACE_AMD_GPU,
    )

    print("Deleting NodeFeatureRule...")
    oc_delete_quiet(
        oc, "nodefeaturerules.nfd.openshift.io", NFD_FEATURE_RULE_NAME,
        "-n", NAMESPACE_AMD_GPU,
    )

    print("Deleting NodeFeatureDiscovery...")
    oc_delete_quiet(
        oc, "nodefeaturediscoveries.nfd.openshift.io", NFD_INSTANCE_NAME,
        "-n", NAMESPACE_NFD,
    )

    time.sleep(5)

    print("Uninstalling AMD GPU Operator...")
    uninstall_operator(oc, NAMESPACE_AMD_GPU, "amd-gpu-operator")

    print("Uninstalling KMM Operator...")
    uninstall_operator(oc, NAMESPACE_KMM, "kmm")

    print("Uninstalling NFD Operator...")
    uninstall_operator(oc, NAMESPACE_NFD, "nfd")

    print("Deleting MachineConfig (amdgpu blacklist)...")
    oc_delete_quiet(oc, "machineconfig", MACHINECONFIG_AMDGPU_BLACKLIST)

    print("Removing AMD GPU node labels...")
    remove_gpu_node_labels(oc)

    print("Resetting image registry to Removed...")
    r = oc.oc(
        "patch", "configs.imageregistry.operator.openshift.io", "cluster",
        "--type=merge",
        '--patch={"spec":{"managementState":"Removed"}}',
        timeout=30,
    )
    if r.returncode != 0:
        print(f"  Warning: failed to reset image registry: {r.stderr or r.stdout}")

    print("Deleting operator namespaces...")
    oc_delete_quiet(oc, "namespace", NAMESPACE_AMD_GPU)
    oc_delete_quiet(oc, "namespace", NAMESPACE_KMM)
    oc_delete_quiet(oc, "namespace", NAMESPACE_NFD)

    print("\n" + "=" * 60)
    print("AMD GPU Operator stack cleanup complete.")
    print("=" * 60)
