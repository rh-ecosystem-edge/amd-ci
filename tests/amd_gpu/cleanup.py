"""Cleanup utilities for AMD GPU operator verification tests.

Mirrors the eco-gotests AfterAll cleanup: removes DeviceConfig, FeatureRule,
operators, MachineConfig, node labels, image registry config, and namespaces
in reverse installation order.
"""

from __future__ import annotations

import logging
import time

from kubernetes import client
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

# Namespaces and resource names (duplicated from constants to keep this
# module self-contained so it can run even if other imports fail).
NAMESPACE_AMD_GPU = "openshift-amd-gpu"
NAMESPACE_KMM = "openshift-kmm"
NAMESPACE_NFD = "openshift-nfd"
DEVICECONFIG_NAME = "amd-gpu-device-config"
NFD_FEATURE_RULE_NAME = "amd-gpu-feature-rule"
MACHINECONFIG_NAME = "amdgpu-module-blacklist"
NFD_INSTANCE_NAME = "amd-gpu-nfd-instance"

# Node labels applied by the AMD GPU Node Labeller
GPU_LABELS_TO_REMOVE = [
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


def delete_custom_object_quiet(
    custom_api: client.CustomObjectsApi,
    group: str,
    version: str,
    namespace: str,
    plural: str,
    name: str,
) -> None:
    """Delete a namespaced custom object, ignoring 404."""
    try:
        custom_api.delete_namespaced_custom_object(
            group, version, namespace, plural, name,
        )
        logger.info("Deleted %s/%s in %s", plural, name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            logger.info("%s/%s not found, skipping", plural, name)
        else:
            logger.warning("Error deleting %s/%s: %s", plural, name, exc)


def delete_cluster_custom_object_quiet(
    custom_api: client.CustomObjectsApi,
    group: str,
    version: str,
    plural: str,
    name: str,
) -> None:
    """Delete a cluster-scoped custom object, ignoring 404."""
    try:
        custom_api.delete_cluster_custom_object(
            group, version, plural, name,
        )
        logger.info("Deleted cluster %s/%s", plural, name)
    except ApiException as exc:
        if exc.status == 404:
            logger.info("Cluster %s/%s not found, skipping", plural, name)
        else:
            logger.warning("Error deleting cluster %s/%s: %s", plural, name, exc)


def delete_namespace_quiet(core_api: client.CoreV1Api, name: str) -> None:
    """Delete a namespace, ignoring 404."""
    try:
        core_api.delete_namespace(name)
        logger.info("Deleted namespace %s", name)
    except ApiException as exc:
        if exc.status == 404:
            logger.info("Namespace %s not found, skipping", name)
        else:
            logger.warning("Error deleting namespace %s: %s", name, exc)


def uninstall_operator(
    custom_api: client.CustomObjectsApi,
    namespace: str,
    subscription_name: str,
) -> None:
    """Uninstall an OLM-managed operator by deleting its Subscription and CSV."""
    # Get installed CSV name from the subscription.
    csv_name = None
    try:
        sub = custom_api.get_namespaced_custom_object(
            "operators.coreos.com", "v1alpha1", namespace,
            "subscriptions", subscription_name,
        )
        csv_name = (sub.get("status") or {}).get("installedCSV", "")
    except ApiException:
        pass

    # Delete subscription.
    delete_custom_object_quiet(
        custom_api, "operators.coreos.com", "v1alpha1",
        namespace, "subscriptions", subscription_name,
    )

    # Delete CSV.
    if csv_name:
        delete_custom_object_quiet(
            custom_api, "operators.coreos.com", "v1alpha1",
            namespace, "clusterserviceversions", csv_name,
        )

    # Delete operator group (best-effort, names may vary).
    try:
        ogs = custom_api.list_namespaced_custom_object(
            "operators.coreos.com", "v1", namespace, "operatorgroups",
        )
        for og in (ogs.get("items") or []):
            og_name = og["metadata"]["name"]
            delete_custom_object_quiet(
                custom_api, "operators.coreos.com", "v1",
                namespace, "operatorgroups", og_name,
            )
    except ApiException:
        pass


def remove_gpu_node_labels(core_api: client.CoreV1Api) -> None:
    """Remove AMD GPU labels from all nodes."""
    nodes = core_api.list_node()
    for node in nodes.items:
        labels = node.metadata.labels or {}
        patch_needed = False
        for lbl in GPU_LABELS_TO_REMOVE:
            if lbl in labels:
                patch_needed = True
                break
        if not patch_needed:
            continue
        # Build JSON patch to remove labels.
        patch_ops = []
        for lbl in GPU_LABELS_TO_REMOVE:
            if lbl in labels:
                escaped = lbl.replace("/", "~1")
                patch_ops.append({"op": "remove", "path": f"/metadata/labels/{escaped}"})
        if patch_ops:
            try:
                core_api.patch_node(
                    node.metadata.name,
                    patch_ops,
                    _content_type="application/json-patch+json",
                )
                logger.info("Removed GPU labels from node %s", node.metadata.name)
            except ApiException as exc:
                logger.warning(
                    "Error removing labels from node %s: %s",
                    node.metadata.name, exc,
                )


def reset_image_registry(custom_api: client.CustomObjectsApi) -> None:
    """Reset the OpenShift internal image registry to Removed state."""
    try:
        custom_api.patch_cluster_custom_object(
            "imageregistry.operator.openshift.io", "v1",
            "configs", "cluster",
            {"spec": {"managementState": "Removed"}},
            _content_type="application/merge-patch+json",
        )
        logger.info("Image registry set to Removed")
    except ApiException as exc:
        logger.warning("Error resetting image registry: %s", exc)


def cleanup_amd_gpu_stack(
    core_api: client.CoreV1Api,
    custom_api: client.CustomObjectsApi,
) -> None:
    """Full cleanup of the AMD GPU stack in reverse installation order.

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
    logger.info("Starting AMD GPU stack cleanup...")

    # 1. Delete DeviceConfig.
    delete_custom_object_quiet(
        custom_api, "amd.com", "v1alpha1",
        NAMESPACE_AMD_GPU, "deviceconfigs", DEVICECONFIG_NAME,
    )

    # 2. Delete NodeFeatureRule.
    delete_custom_object_quiet(
        custom_api, "nfd.openshift.io", "v1alpha1",
        NAMESPACE_AMD_GPU, "nodefeaturerules", NFD_FEATURE_RULE_NAME,
    )

    # 3. Delete NodeFeatureDiscovery.
    delete_custom_object_quiet(
        custom_api, "nfd.openshift.io", "v1",
        NAMESPACE_NFD, "nodefeaturediscoveries", NFD_INSTANCE_NAME,
    )

    # Give a moment for resources to settle.
    time.sleep(5)

    # 4. Uninstall AMD GPU Operator.
    logger.info("Uninstalling AMD GPU Operator...")
    uninstall_operator(custom_api, NAMESPACE_AMD_GPU, "amd-gpu-operator")

    # 5. Uninstall KMM Operator.
    logger.info("Uninstalling KMM Operator...")
    uninstall_operator(custom_api, NAMESPACE_KMM, "kmm")

    # 6. Uninstall NFD Operator.
    logger.info("Uninstalling NFD Operator...")
    uninstall_operator(custom_api, NAMESPACE_NFD, "nfd")

    # 7. Delete MachineConfig.
    delete_cluster_custom_object_quiet(
        custom_api, "machineconfiguration.openshift.io", "v1",
        "machineconfigs", MACHINECONFIG_NAME,
    )

    # 8. Remove GPU node labels.
    logger.info("Removing AMD GPU node labels...")
    remove_gpu_node_labels(core_api)

    # 9. Reset image registry.
    logger.info("Resetting image registry...")
    reset_image_registry(custom_api)

    # 10. Delete namespaces.
    logger.info("Deleting operator namespaces...")
    delete_namespace_quiet(core_api, NAMESPACE_AMD_GPU)
    delete_namespace_quiet(core_api, NAMESPACE_KMM)
    delete_namespace_quiet(core_api, NAMESPACE_NFD)

    logger.info("AMD GPU stack cleanup complete.")
