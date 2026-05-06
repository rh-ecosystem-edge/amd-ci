"""AMD GPU metrics exporter verification tests.

Validates that the AMD GPU metrics exporter is running, its Service exists,
and the /metrics endpoint exposes valid Prometheus-formatted GPU telemetry.

Prerequisites (in addition to those in test_amd_gpu_basic.py):
    - DeviceConfig CR created with ``metricsExporter.enable: true``.
    - Metrics exporter DaemonSet pods Running on all AMD GPU nodes.
"""

from __future__ import annotations

import logging

import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException

from tests.amd_gpu.constants import (
    EXPECTED_METRICS,
    METRICS_EXPORTER_PREFIX,
    NAMESPACE_AMD_GPU,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.amd_gpu


# ============================================================================
# Metrics Exporter
# ============================================================================


class TestMetricsExporter:
    """Verify the AMD GPU metrics exporter is operational."""

    def _find_metrics_service(
        self, k8s_core_api: client.CoreV1Api
    ) -> client.V1Service | None:
        """Return the metrics-exporter Service, or None."""
        services = k8s_core_api.list_namespaced_service(NAMESPACE_AMD_GPU)
        expected_prefix = METRICS_EXPORTER_PREFIX.removesuffix("-")
        matches = [
            s
            for s in services.items
            if (s.metadata.name or "").startswith(expected_prefix)
        ]
        return matches[0] if matches else None

    def test_metrics_exporter_pods_running(
        self,
        k8s_core_api: client.CoreV1Api,
        amd_gpu_nodes: list[client.V1Node],
    ) -> None:
        """One metrics exporter pod per AMD GPU node, all Running and ready."""
        pods = k8s_core_api.list_namespaced_pod(NAMESPACE_AMD_GPU)
        exporter_pods = [
            p
            for p in pods.items
            if (p.metadata.name or "").startswith(METRICS_EXPORTER_PREFIX)
        ]

        assert exporter_pods, (
            f"No metrics exporter pods found with prefix '{METRICS_EXPORTER_PREFIX}' "
            f"in namespace '{NAMESPACE_AMD_GPU}'"
        )
        assert len(exporter_pods) == len(amd_gpu_nodes), (
            f"Expected {len(amd_gpu_nodes)} metrics exporter pod(s) "
            f"(one per AMD GPU node), found {len(exporter_pods)}"
        )
        gpu_node_names = {n.metadata.name for n in amd_gpu_nodes}
        exporter_node_names = {
            p.spec.node_name for p in exporter_pods if p.spec and p.spec.node_name
        }
        assert exporter_node_names == gpu_node_names, (
            "Metrics exporter pod placement mismatch. "
            f"Expected nodes={sorted(gpu_node_names)}, got={sorted(exporter_node_names)}"
        )

        for pod in exporter_pods:
            assert pod.status.phase == "Running", (
                f"Metrics exporter pod {pod.metadata.name} has phase "
                f"'{pod.status.phase}', expected 'Running'"
            )
            for cs in pod.status.container_statuses or []:
                assert cs.ready, (
                    f"Container '{cs.name}' in pod {pod.metadata.name} is not ready"
                )
            logger.info(
                "Metrics exporter pod %s is Running and ready", pod.metadata.name
            )

    def test_metrics_service_exists(self, k8s_core_api: client.CoreV1Api) -> None:
        """A metrics exporter Service must exist in the AMD GPU namespace."""
        svc = self._find_metrics_service(k8s_core_api)
        assert svc is not None, (
            f"No metrics exporter service found in namespace '{NAMESPACE_AMD_GPU}' "
            "(expected a service with 'metrics' in its name)"
        )
        logger.info("Found metrics service: %s", svc.metadata.name)

    def test_metrics_endpoint_reachable(self, k8s_core_api: client.CoreV1Api) -> None:
        """The /metrics endpoint must return a valid Prometheus text response."""
        svc = self._find_metrics_service(k8s_core_api)
        if svc is None:
            pytest.skip(
                "Metrics exporter service not found — skipping endpoint reachability test"
            )

        svc_name = svc.metadata.name
        port = svc.spec.ports[0].port if svc.spec.ports else None
        proxy_name = f"{svc_name}:{port}" if port else svc_name

        try:
            body = k8s_core_api.connect_get_namespaced_service_proxy_with_path(
                name=proxy_name,
                namespace=NAMESPACE_AMD_GPU,
                path="metrics",
            )
        except ApiException as exc:
            pytest.fail(
                f"Failed to reach /metrics on service '{svc_name}': "
                f"HTTP {exc.status} — {exc.reason}"
            )

        assert body, f"Empty response from '{svc_name}/metrics'"
        assert "# HELP" in body or "# TYPE" in body, (
            f"Response from '{svc_name}/metrics' is not valid Prometheus format"
        )
        logger.info(
            "Metrics endpoint reachable on '%s', response length=%d",
            svc_name,
            len(body),
        )

    def test_core_gpu_metrics_present(self, k8s_core_api: client.CoreV1Api) -> None:
        """Core AMD GPU metrics (temperature, power, memory, activity) must be
        present in the /metrics output.
        """
        svc = self._find_metrics_service(k8s_core_api)
        if svc is None:
            pytest.skip(
                "Metrics exporter service not found — skipping metric content test"
            )

        svc_name = svc.metadata.name
        port = svc.spec.ports[0].port if svc.spec.ports else None
        proxy_name = f"{svc_name}:{port}" if port else svc_name

        try:
            body = k8s_core_api.connect_get_namespaced_service_proxy_with_path(
                name=proxy_name,
                namespace=NAMESPACE_AMD_GPU,
                path="metrics",
            )
        except ApiException as exc:
            pytest.skip(
                f"Cannot reach /metrics on service '{svc_name}': "
                f"HTTP {exc.status} — skipping metric content checks"
            )

        metric_names = {
            line.split("{", 1)[0].split()[0]
            for line in body.splitlines()
            if line and not line.startswith("#")
        }
        missing = [m for m in EXPECTED_METRICS if m not in metric_names]
        assert not missing, (
            f"The following expected metrics were not found in /metrics output: {missing}"
        )

        for metric in EXPECTED_METRICS:
            logger.info("Confirmed expected metric present: %s", metric)
