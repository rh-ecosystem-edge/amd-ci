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
import time

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
    METRICS_EXPORTER_PREFIX,
    NAMESPACE_AMD_GPU,
    NAMESPACE_IMAGE_REGISTRY,
    NFD_LABEL_KEY,
    NFD_LABEL_VALUE,
    NODE_LABELLER_LABELS,
    NODE_LABELLER_PREFIX,
)
from tests.amd_gpu.helpers import (
    patch_device_config,
    run_gpu_command,
    wait_for_pods_gone,
    wait_for_pods_running_by_prefix,
)

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
        """At least one Node Labeller pod must be Running.

        Waits up to 3 minutes — the labeller DaemonSet may still be
        scheduling when the test suite starts.
        """
        wait_for_pods_running_by_prefix(
            k8s_core_api, NAMESPACE_AMD_GPU, NODE_LABELLER_PREFIX,
            min_count=1, timeout=180,
        )
        pods = k8s_core_api.list_namespaced_pod(NAMESPACE_AMD_GPU)
        labeller_pods = [
            p
            for p in pods.items
            if p.metadata.name.startswith(NODE_LABELLER_PREFIX)
        ]
        for pod in labeller_pods:
            assert pod.status.phase == "Running", (
                f"Node Labeller pod {pod.metadata.name} has phase "
                f"'{pod.status.phase}', expected 'Running'"
            )
            logger.info("Node Labeller pod %s is Running", pod.metadata.name)

    def test_gpu_metadata_labels_applied(self, k8s_core_api):
        """All expected GPU metadata labels must be present on every AMD
        GPU node, and the device-id value must be a known AMD GPU.

        Polls up to 3 minutes because the node-labeller applies labels
        asynchronously after its pod reaches Running.
        """
        _LABEL_PRESENT_TIMEOUT = 180
        _LABEL_PRESENT_POLL = 5

        deadline = time.monotonic() + _LABEL_PRESENT_TIMEOUT
        missing_per_node: dict[str, list[str]] = {}
        while time.monotonic() < deadline:
            nodes = k8s_core_api.list_node(
                label_selector=f"{NFD_LABEL_KEY}={NFD_LABEL_VALUE}",
            )
            assert nodes.items, "No AMD GPU nodes found when checking labels"
            missing_per_node = {}
            for node in nodes.items:
                labels = node.metadata.labels or {}
                missing = [lbl for lbl in NODE_LABELLER_LABELS if lbl not in labels]
                if missing:
                    missing_per_node[node.metadata.name] = missing
            if not missing_per_node:
                break
            logger.debug("Waiting for GPU labels: %s", missing_per_node)
            time.sleep(_LABEL_PRESENT_POLL)

        assert not missing_per_node, (
            f"GPU metadata labels not applied within {_LABEL_PRESENT_TIMEOUT}s. "
            f"Missing labels per node: {missing_per_node}"
        )

        nodes = k8s_core_api.list_node(
            label_selector=f"{NFD_LABEL_KEY}={NFD_LABEL_VALUE}",
        )
        for node in nodes.items:
            labels = node.metadata.labels or {}
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


# ============================================================================
# Component Cleanup (disable → verify cleanup → restore)
# ============================================================================

_LABEL_POLL_INTERVAL = 5
_LABEL_ABSENT_TIMEOUT = 120


class TestComponentCleanup:
    """Verify that disabling a DeviceConfig component removes its pods and
    associated resources, then restore the original configuration.

    Each test disables a component, asserts the cleanup, and re-enables it
    inside a ``try/finally`` so the cluster is left healthy even on failure.
    """

    def test_node_labeller_disable(
        self, k8s_core_api, k8s_custom_api, amd_gpu_nodes
    ):
        """Disabling ``enableNodeLabeller`` must remove labeller pods and
        GPU metadata labels from all AMD GPU nodes.
        """
        patch_device_config(
            k8s_custom_api,
            {"spec": {"devicePlugin": {"enableNodeLabeller": False}}},
        )
        logger.info("Disabled node labeller in DeviceConfig")

        try:
            # Pods must terminate.
            wait_for_pods_gone(k8s_core_api, NAMESPACE_AMD_GPU, NODE_LABELLER_PREFIX)
            logger.info("Node labeller pods are gone")

            pods = k8s_core_api.list_namespaced_pod(NAMESPACE_AMD_GPU).items
            remaining = [
                p.metadata.name
                for p in pods
                if p.metadata.name.startswith(NODE_LABELLER_PREFIX)
            ]
            assert not remaining, (
                f"Node labeller pods still present after disabling: {remaining}"
            )

            # GPU metadata labels must be removed from all AMD GPU nodes.
            node_names = {n.metadata.name for n in amd_gpu_nodes}
            deadline = time.monotonic() + _LABEL_ABSENT_TIMEOUT
            violations: dict[str, list[str]] = {}
            while time.monotonic() < deadline:
                violations = {}
                for node in k8s_core_api.list_node().items:
                    if node.metadata.name not in node_names:
                        continue
                    labels = node.metadata.labels or {}
                    still_present = [
                        lbl for lbl in NODE_LABELLER_LABELS if lbl in labels
                    ]
                    if still_present:
                        violations[node.metadata.name] = still_present
                if not violations:
                    break
                logger.debug("Waiting for GPU labels to be removed: %s", violations)
                time.sleep(_LABEL_POLL_INTERVAL)

            assert not violations, (
                f"GPU labels still present on nodes after disabling node labeller "
                f"(waited {_LABEL_ABSENT_TIMEOUT}s): {violations}"
            )
            logger.info("GPU metadata labels removed from all AMD GPU nodes")

        finally:
            patch_device_config(
                k8s_custom_api,
                {"spec": {"devicePlugin": {"enableNodeLabeller": True}}},
            )
            logger.info("Re-enabled node labeller; waiting for pods to come back")
            wait_for_pods_running_by_prefix(
                k8s_core_api, NAMESPACE_AMD_GPU, NODE_LABELLER_PREFIX
            )

    def test_metrics_exporter_disable(self, k8s_core_api, k8s_custom_api):
        """Disabling ``metricsExporter`` must remove its pods, Service, and
        ServiceMonitor (when Prometheus CRDs are installed).
        """
        patch_device_config(
            k8s_custom_api,
            {"spec": {"metricsExporter": {"enable": False}}},
        )
        logger.info("Disabled metrics exporter in DeviceConfig")

        # Derive the expected service name base from the pod prefix.
        service_name_base = METRICS_EXPORTER_PREFIX.rstrip("-")

        try:
            # Pods must terminate.
            wait_for_pods_gone(
                k8s_core_api, NAMESPACE_AMD_GPU, METRICS_EXPORTER_PREFIX
            )
            logger.info("Metrics exporter pods are gone")

            pods = k8s_core_api.list_namespaced_pod(NAMESPACE_AMD_GPU).items
            remaining_pods = [
                p.metadata.name
                for p in pods
                if p.metadata.name.startswith(METRICS_EXPORTER_PREFIX)
            ]
            assert not remaining_pods, (
                f"Metrics exporter pods still present after disabling: {remaining_pods}"
            )

            # Service must be removed.
            services = k8s_core_api.list_namespaced_service(NAMESPACE_AMD_GPU).items
            remaining_svcs = [
                svc.metadata.name
                for svc in services
                if svc.metadata.name.startswith(service_name_base)
            ]
            assert not remaining_svcs, (
                f"Metrics exporter Service(s) still present after disabling: "
                f"{remaining_svcs}"
            )
            logger.info("Metrics exporter Service removed")

            # ServiceMonitor must be removed (best-effort: skip if CRD absent).
            try:
                monitors = k8s_custom_api.list_namespaced_custom_object(
                    "monitoring.coreos.com",
                    "v1",
                    NAMESPACE_AMD_GPU,
                    "servicemonitors",
                )
                remaining_sm = [
                    sm["metadata"]["name"]
                    for sm in (monitors.get("items") or [])
                ]
                assert not remaining_sm, (
                    f"ServiceMonitor(s) still present after disabling metrics "
                    f"exporter: {remaining_sm}"
                )
                logger.info("ServiceMonitor removed")
            except ApiException as exc:
                if exc.status == 404:
                    logger.info(
                        "ServiceMonitor CRD not present; skipping ServiceMonitor check"
                    )
                else:
                    raise

        finally:
            patch_device_config(
                k8s_custom_api,
                {"spec": {"metricsExporter": {"enable": True}}},
            )
            logger.info("Re-enabled metrics exporter; waiting for pods to come back")
            wait_for_pods_running_by_prefix(
                k8s_core_api, NAMESPACE_AMD_GPU, METRICS_EXPORTER_PREFIX
            )
