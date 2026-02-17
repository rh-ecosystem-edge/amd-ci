"""AMD GPU Operator basic verification tests.

Validates that the AMD GPU Operator and its dependencies (NFD, KMM) are
correctly installed and functioning on an OpenShift cluster with AMD GPU
hardware.

Prerequisites (assumed to be completed before running these tests):
    - OpenShift cluster with AMD GPU hardware.
    - NFD, KMM, and AMD GPU Operator installed via OLM.
    - DeviceConfig CR created in ``openshift-amd-gpu`` namespace.
    - ``KUBECONFIG`` environment variable pointing to the cluster.

Equivalent to the Ginkgo ``AMD GPU Basic Tests`` suite in
``eco-gotests/tests/hw-accel/amdgpu/basic/tests/basic-test.go``.
"""

from __future__ import annotations

import logging
import re

import pytest
from kubernetes.client.rest import ApiException

from tests.amd_gpu.constants import (
    DEVICE_IDS,
    DEVICE_PLUGIN_PREFIX,
    DEVICECONFIG_GROUP,
    DEVICECONFIG_NAME,
    DEVICECONFIG_PLURAL,
    DEVICECONFIG_VERSION,
    GPU_RESOURCE_NAME,
    NAMESPACE_AMD_GPU,
    NAMESPACE_IMAGE_REGISTRY,
    NFD_LABEL_KEY,
    NFD_LABEL_VALUE,
    NODE_LABELLER_LABELS,
    NODE_LABELLER_PREFIX,
)
from tests.amd_gpu.helpers import run_gpu_command

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.amd_gpu


# ============================================================================
# Internal Image Registry
# ============================================================================


class TestInternalRegistry:
    """Verify the OpenShift internal image registry is available."""

    def test_registry_pods_running(self, k8s_core_api):
        """At least one image-registry pod must be Running with all
        containers ready.
        """
        pods = k8s_core_api.list_namespaced_pod(
            NAMESPACE_IMAGE_REGISTRY,
            label_selector="docker-registry=default",
        )

        running_count = 0
        for pod in pods.items:
            if pod.status.phase != "Running":
                continue
            statuses = pod.status.container_statuses or []
            if statuses and all(cs.ready for cs in statuses):
                running_count += 1

        assert running_count > 0, (
            "No running image-registry pods found in "
            f"{NAMESPACE_IMAGE_REGISTRY} (label: docker-registry=default)"
        )
        logger.info(
            "Internal registry verified: %d pod(s) running", running_count
        )


# ============================================================================
# Node Feature Discovery Labels
# ============================================================================


class TestNFDLabels:
    """Verify NFD has labelled nodes that contain AMD GPUs."""

    def test_nfd_labels_applied(self, k8s_core_api):
        """All worker nodes must carry the NFD label
        ``feature.node.kubernetes.io/amd-gpu=true``.

        Queries worker nodes directly (not the pre-filtered fixture) to
        verify NFD actually discovered AMD GPU hardware on every worker.
        """
        workers = k8s_core_api.list_node(
            label_selector="node-role.kubernetes.io/worker=",
        )
        # Fall back to all nodes for SNO clusters where the single node
        # has both control-plane and worker roles but may lack the
        # explicit worker label.
        if not workers.items:
            workers = k8s_core_api.list_node()

        assert workers.items, "No worker nodes found in the cluster"

        unlabelled = [
            n.metadata.name
            for n in workers.items
            if (n.metadata.labels or {}).get(NFD_LABEL_KEY) != NFD_LABEL_VALUE
        ]

        assert not unlabelled, (
            f"Not all worker nodes have the NFD label "
            f"{NFD_LABEL_KEY}={NFD_LABEL_VALUE}. "
            f"Unlabelled nodes: {unlabelled}. "
            "Verify NFD is running and the AMD GPU FeatureRule is created."
        )

        logger.info(
            "NFD label verified on all %d worker node(s)",
            len(workers.items),
        )


# ============================================================================
# DeviceConfig Custom Resource
# ============================================================================


class TestDeviceConfig:
    """Verify the DeviceConfig CR exists and is accessible."""

    def test_device_config_exists(self, k8s_custom_api):
        """DeviceConfig CR must exist in the AMD GPU namespace."""
        try:
            dc = k8s_custom_api.get_namespaced_custom_object(
                DEVICECONFIG_GROUP,
                DEVICECONFIG_VERSION,
                NAMESPACE_AMD_GPU,
                DEVICECONFIG_PLURAL,
                DEVICECONFIG_NAME,
            )
        except ApiException as exc:
            if exc.status == 404:
                pytest.fail(
                    f"DeviceConfig '{DEVICECONFIG_NAME}' not found in "
                    f"namespace '{NAMESPACE_AMD_GPU}'"
                )
            raise

        assert dc["metadata"]["name"] == DEVICECONFIG_NAME
        logger.info(
            "DeviceConfig '%s' exists — status: %s",
            DEVICECONFIG_NAME,
            dc.get("status", "N/A"),
        )


# ============================================================================
# Node Labeller
# ============================================================================


class TestNodeLabeller:
    """Verify Node Labeller pods and GPU metadata labels."""

    def test_node_labeller_pods_running(self, k8s_core_api):
        """At least one Node Labeller pod must be Running."""
        pods = k8s_core_api.list_namespaced_pod(NAMESPACE_AMD_GPU)
        labeller_pods = [
            p
            for p in pods.items
            if p.metadata.name.startswith(NODE_LABELLER_PREFIX)
        ]

        assert labeller_pods, (
            f"No Node Labeller pods found with prefix '{NODE_LABELLER_PREFIX}' "
            f"in namespace '{NAMESPACE_AMD_GPU}'"
        )

        for pod in labeller_pods:
            assert pod.status.phase == "Running", (
                f"Node Labeller pod {pod.metadata.name} has phase "
                f"'{pod.status.phase}', expected 'Running'"
            )
            logger.info(
                "Node Labeller pod %s is Running", pod.metadata.name
            )

    def test_gpu_metadata_labels_applied(self, k8s_core_api):
        """All expected GPU metadata labels must be present on every AMD
        GPU node, and the device-id value must be a known AMD GPU.
        """
        nodes = k8s_core_api.list_node(
            label_selector=f"{NFD_LABEL_KEY}={NFD_LABEL_VALUE}",
        )
        assert nodes.items, "No AMD GPU nodes found when checking labels"

        for node in nodes.items:
            labels = node.metadata.labels or {}
            missing = [
                lbl for lbl in NODE_LABELLER_LABELS if lbl not in labels
            ]
            assert not missing, (
                f"Node {node.metadata.name} is missing GPU labels: {missing}"
            )

            device_id = labels.get("amd.com/gpu.device-id", "")
            assert device_id, (
                f"Node {node.metadata.name}: label 'amd.com/gpu.device-id' "
                f"is present but empty"
            )
            assert device_id in DEVICE_IDS, (
                f"Node {node.metadata.name}: unknown device-id "
                f"'{device_id}'. Known IDs: {list(DEVICE_IDS)}"
            )
            logger.info(
                "Node %s: device-id=%s (%s)",
                node.metadata.name,
                device_id,
                DEVICE_IDS[device_id],
            )


# ============================================================================
# Device Plugin
# ============================================================================


class TestDevicePlugin:
    """Verify Device Plugin pods and GPU resource reporting."""

    def test_device_plugin_pods_running(self, k8s_core_api, amd_gpu_nodes):
        """One healthy Device Plugin pod per AMD GPU node, all Running."""
        pods = k8s_core_api.list_namespaced_pod(NAMESPACE_AMD_GPU)
        dp_pods = [
            p
            for p in pods.items
            if p.metadata.name.startswith(DEVICE_PLUGIN_PREFIX)
        ]

        assert len(dp_pods) == len(amd_gpu_nodes), (
            f"Expected {len(amd_gpu_nodes)} device-plugin pod(s) "
            f"(one per AMD GPU node), found {len(dp_pods)}"
        )

        for pod in dp_pods:
            assert pod.status.phase == "Running", (
                f"Device Plugin pod {pod.metadata.name} has phase "
                f"'{pod.status.phase}', expected 'Running'"
            )
            for cs in pod.status.container_statuses or []:
                assert cs.ready, (
                    f"Container '{cs.name}' in pod {pod.metadata.name} "
                    "is not ready"
                )
            logger.info(
                "Device Plugin pod %s is Running and healthy",
                pod.metadata.name,
            )

    def test_gpu_resources_available(self, k8s_core_api):
        """Every AMD GPU node must report ``amd.com/gpu >= 1`` in both
        capacity and allocatable.
        """
        nodes = k8s_core_api.list_node(
            label_selector=f"{NFD_LABEL_KEY}={NFD_LABEL_VALUE}",
        )
        assert nodes.items, "No AMD GPU nodes found when checking resources"

        for node in nodes.items:
            capacity = int(
                (node.status.capacity or {}).get(GPU_RESOURCE_NAME, "0")
            )
            allocatable = int(
                (node.status.allocatable or {}).get(GPU_RESOURCE_NAME, "0")
            )

            assert capacity >= 1, (
                f"Node {node.metadata.name}: GPU capacity is {capacity}, "
                "expected >= 1"
            )
            assert allocatable >= 1, (
                f"Node {node.metadata.name}: GPU allocatable is "
                f"{allocatable}, expected >= 1"
            )
            logger.info(
                "Node %s: GPU capacity=%d, allocatable=%d",
                node.metadata.name,
                capacity,
                allocatable,
            )


# ============================================================================
# ROCm Tool Validation
# ============================================================================


class TestROCmValidation:
    """Verify GPU detection and information via ROCm tools."""

    def test_rocm_smi_detects_gpus(self, k8s_core_api):
        """``rocm-smi`` must report at least one GPU.

        GPU entries in ``rocm-smi`` output start with a digit (the GPU
        index).
        """
        output = run_gpu_command(
            k8s_core_api,
            pod_name="amd-gpu-smi-test",
            command=["rocm-smi"],
        )
        logger.info("rocm-smi output:\n%s", output)

        gpu_found = any(
            line.strip() and line.strip()[0].isdigit()
            for line in output.splitlines()
        )
        assert gpu_found, (
            "Expected at least one GPU entry (line starting with a digit) "
            f"in rocm-smi output:\n{output}"
        )

    def test_rocminfo_validates_gpu(self, k8s_core_api):
        """``rocminfo`` output must contain GPU architecture, agent info,
        and AMD vendor string.
        """
        output = run_gpu_command(
            k8s_core_api,
            pod_name="amd-gpu-info-test",
            command=["rocminfo"],
        )
        logger.info("rocminfo output:\n%s", output)

        assert "gfx" in output, (
            "Expected GPU architecture string 'gfx' in rocminfo output"
        )
        assert re.search(r"GPU|Agent\s+\d+", output), (
            "Expected GPU/Agent info in rocminfo output"
        )
        assert "AMD" in output, (
            "Expected 'AMD' vendor string in rocminfo output"
        )
