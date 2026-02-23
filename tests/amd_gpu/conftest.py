"""Pytest fixtures for AMD GPU operator verification tests."""

from __future__ import annotations

import logging
import os

import pytest
from kubernetes import client, config

from tests.amd_gpu.constants import NFD_LABEL_KEY, NFD_LABEL_VALUE

logger = logging.getLogger(__name__)


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "amd_gpu: AMD GPU operator verification tests")


# ---------------------------------------------------------------------------
# Kubernetes client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def load_kubeconfig():
    """Load Kubernetes configuration once per session.

    When ``KUBECONFIG`` is set explicitly, honour it even when running
    inside a cluster so that tests target the intended cluster (e.g. a
    remote GPU cluster reached via an SSH tunnel) rather than the CI
    build cluster.
    """
    kubeconfig_env = os.environ.get("KUBECONFIG")
    if kubeconfig_env:
        try:
            config.load_kube_config(config_file=kubeconfig_env)
            return
        except config.ConfigException as exc:
            pytest.fail(
                f"KUBECONFIG is set ({kubeconfig_env}) but could not be loaded: {exc}"
            )
    try:
        config.load_incluster_config()
        return
    except config.ConfigException:
        pass
    try:
        config.load_kube_config()
    except config.ConfigException as exc:
        pytest.fail(
            f"Cannot load Kubernetes config. "
            f"Set KUBECONFIG or run inside a cluster. Error: {exc}"
        )


@pytest.fixture(scope="session")
def k8s_core_api(load_kubeconfig) -> client.CoreV1Api:
    """Return a CoreV1Api client."""
    return client.CoreV1Api()


@pytest.fixture(scope="session")
def k8s_custom_api(load_kubeconfig) -> client.CustomObjectsApi:
    """Return a CustomObjectsApi client."""
    return client.CustomObjectsApi()


# ---------------------------------------------------------------------------
# Cluster topology fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def amd_gpu_nodes(k8s_core_api: client.CoreV1Api) -> list[client.V1Node]:
    """Return nodes labelled with the AMD GPU NFD label.

    Skips the entire session if no AMD GPU nodes are found.
    """
    nodes = k8s_core_api.list_node(
        label_selector=f"{NFD_LABEL_KEY}={NFD_LABEL_VALUE}",
    )
    if not nodes.items:
        pytest.skip("No AMD GPU nodes found in cluster")

    logger.info("Found %d AMD GPU node(s)", len(nodes.items))
    for node in nodes.items:
        logger.info("  AMD GPU node: %s", node.metadata.name)
    return nodes.items

