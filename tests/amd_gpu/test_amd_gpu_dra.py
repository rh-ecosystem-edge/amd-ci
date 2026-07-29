"""DRA (Dynamic Resource Allocation) tests for AMD GPU operator.

Tests cover three layers:
  1. DRA driver DaemonSet pods are Running
  2. DeviceClass and ResourceSlice are published to the API
  3. A GPU can be allocated via ResourceClaim and used inside a pod
"""

from __future__ import annotations

import logging

import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException

from tests.amd_gpu.constants import (
    DRA_DEVICE_CLASS_NAME,
    DRA_DRIVER_NAME,
    DRA_DRIVER_PREFIX,
    DRA_RESOURCE_GROUP,
    DRA_RESOURCE_VERSION,
    NAMESPACE_AMD_GPU,
    ROCM_TEST_IMAGE,
)
from tests.amd_gpu.helpers import (
    delete_pod_if_exists,
    wait_for_pod_done,
    wait_for_pods_running_by_prefix,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.amd_gpu

_DRA_TEST_NS = "default"
_DRA_CLAIM_NAME = "dra-test-gpu-claim"
_DRA_POD_NAME = "dra-test-gpu-pod"


@pytest.mark.usefixtures("require_dra")
class TestDRADriver:
    """DRA driver DaemonSet pods are deployed and healthy."""

    def test_dra_driver_pods_running(
        self,
        k8s_core_api: client.CoreV1Api,
        amd_gpu_nodes: list,
    ) -> None:
        wait_for_pods_running_by_prefix(
            k8s_core_api,
            NAMESPACE_AMD_GPU,
            DRA_DRIVER_PREFIX,
            min_count=1,
            timeout=300,
        )
        pods = k8s_core_api.list_namespaced_pod(NAMESPACE_AMD_GPU).items
        running = [
            p.metadata.name
            for p in pods
            if p.metadata.name.startswith(DRA_DRIVER_PREFIX)
            and p.status.phase == "Running"
        ]
        logger.info("DRA driver pods running: %s", running)
        assert running, (
            f"No Running DRA driver pods with prefix '{DRA_DRIVER_PREFIX}' "
            f"in namespace '{NAMESPACE_AMD_GPU}'"
        )


@pytest.mark.usefixtures("require_dra")
class TestDRAResources:
    """DRA resource model is populated by the driver."""

    def test_device_class_exists(
        self,
        k8s_custom_api: client.CustomObjectsApi,
        amd_gpu_nodes: list,
    ) -> None:
        result = k8s_custom_api.list_cluster_custom_object(
            DRA_RESOURCE_GROUP, DRA_RESOURCE_VERSION, "deviceclasses"
        )
        names = [item["metadata"]["name"] for item in result.get("items", [])]
        logger.info("DeviceClasses found: %s", names)
        assert DRA_DEVICE_CLASS_NAME in names, (
            f"DeviceClass '{DRA_DEVICE_CLASS_NAME}' not found. Present: {names}"
        )

    def test_resource_slice_published(
        self,
        k8s_custom_api: client.CustomObjectsApi,
        amd_gpu_nodes: list,
    ) -> None:
        result = k8s_custom_api.list_cluster_custom_object(
            DRA_RESOURCE_GROUP, DRA_RESOURCE_VERSION, "resourceslices"
        )
        amd_slices = [
            item
            for item in result.get("items", [])
            if item.get("spec", {}).get("driver") == DRA_DRIVER_NAME
        ]
        logger.info("AMD ResourceSlices found: %d", len(amd_slices))
        assert amd_slices, (
            f"No ResourceSlices published by driver '{DRA_DRIVER_NAME}'"
        )

        total_devices = sum(
            len(s.get("spec", {}).get("devices", [])) for s in amd_slices
        )
        logger.info("Total devices reported in ResourceSlices: %d", total_devices)
        assert total_devices >= 1, (
            "ResourceSlices exist but report 0 devices"
        )


@pytest.mark.usefixtures("require_dra")
class TestDRAAllocation:
    """GPU can be allocated and used via DRA ResourceClaim."""

    @pytest.fixture(autouse=True)
    def cleanup_dra_resources(
        self,
        k8s_core_api: client.CoreV1Api,
        k8s_custom_api: client.CustomObjectsApi,
    ):
        self._cleanup(k8s_core_api, k8s_custom_api)
        yield
        self._cleanup(k8s_core_api, k8s_custom_api)

    def _cleanup(
        self,
        core_api: client.CoreV1Api,
        custom_api: client.CustomObjectsApi,
    ) -> None:
        delete_pod_if_exists(core_api, _DRA_POD_NAME, _DRA_TEST_NS)
        try:
            custom_api.delete_namespaced_custom_object(
                DRA_RESOURCE_GROUP,
                DRA_RESOURCE_VERSION,
                _DRA_TEST_NS,
                "resourceclaims",
                _DRA_CLAIM_NAME,
            )
        except ApiException as exc:
            if exc.status != 404:
                raise

    def test_gpu_allocation_via_claim(
        self,
        k8s_core_api: client.CoreV1Api,
        k8s_custom_api: client.CustomObjectsApi,
        amd_gpu_nodes: list,
    ) -> None:
        # resource.k8s.io/v1 with the 'exactly' wrapper requires OCP 4.21+ (K8s 1.34+).
        claim_manifest = {
            "apiVersion": f"{DRA_RESOURCE_GROUP}/{DRA_RESOURCE_VERSION}",
            "kind": "ResourceClaim",
            "metadata": {"name": _DRA_CLAIM_NAME, "namespace": _DRA_TEST_NS},
            "spec": {
                "devices": {
                    "requests": [{
                        "name": "gpu",
                        "exactly": {"deviceClassName": DRA_DEVICE_CLASS_NAME},
                    }]
                }
            },
        }
        k8s_custom_api.create_namespaced_custom_object(
            DRA_RESOURCE_GROUP,
            DRA_RESOURCE_VERSION,
            _DRA_TEST_NS,
            "resourceclaims",
            claim_manifest,
        )
        logger.info("Created ResourceClaim %s/%s", _DRA_TEST_NS, _DRA_CLAIM_NAME)

        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": _DRA_POD_NAME, "namespace": _DRA_TEST_NS},
            "spec": {
                "restartPolicy": "Never",
                "terminationGracePeriodSeconds": 1,
                "resourceClaims": [
                    {"name": "gpu", "resourceClaimName": _DRA_CLAIM_NAME}
                ],
                "containers": [{
                    "name": "gpu-test",
                    "image": ROCM_TEST_IMAGE,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["rocminfo"],
                    "resources": {"claims": [{"name": "gpu"}]},
                }],
            },
        }
        k8s_core_api.create_namespaced_pod(_DRA_TEST_NS, pod_manifest)
        logger.info("Created DRA test pod %s/%s", _DRA_TEST_NS, _DRA_POD_NAME)

        phase = wait_for_pod_done(
            k8s_core_api, _DRA_POD_NAME, _DRA_TEST_NS, timeout=300
        )
        logs = k8s_core_api.read_namespaced_pod_log(_DRA_POD_NAME, _DRA_TEST_NS)
        logger.info(
            "DRA test pod finished: phase=%s\nrocminfo output (first 800 chars):\n%s",
            phase,
            logs[:800],
        )

        assert phase == "Succeeded", (
            f"DRA GPU test pod failed (phase={phase}). Logs:\n{logs}"
        )
        assert "Device Type:             GPU" in logs, (
            "rocminfo did not report any GPU HSA agent — GPU may not have been "
            f"injected into the container via DRA. Logs:\n{logs}"
        )
