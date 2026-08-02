"""Pytest fixtures for AMD GPU operator verification tests."""

from __future__ import annotations

import logging
import os
import time

import pytest
from kubernetes import client, config

from tests.amd_gpu.constants import (
    DEVICE_PLUGIN_PREFIX,
    DEVICECONFIG_GROUP,
    DEVICECONFIG_NAME,
    DEVICECONFIG_PLURAL,
    DEVICECONFIG_VERSION,
    DRA_DRIVER_PREFIX,
    GPU_RESOURCE_NAME,
    NAMESPACE_AMD_GPU,
    NFD_LABEL_KEY,
    NFD_LABEL_VALUE,
    NODE_LABELLER_PREFIX,
)
from tests.amd_gpu.helpers import (
    patch_device_config,
    wait_for_pods_gone,
    wait_for_pods_running_by_prefix,
)

logger = logging.getLogger(__name__)

_MODE_SWITCH_TIMEOUT = 300

# Mutable so class-scoped fixtures can update it after a switch.
_session_mode: list[str] = ["unknown"]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "amd_gpu: AMD GPU operator verification tests")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Sort DRA tests before device-plugin tests to minimise mode switches."""
    def _key(item: pytest.Item) -> int:
        names = getattr(item, "fixturenames", [])
        if "require_dra" in names:
            return 0
        if "require_device_plugin" in names:
            return 2
        return 1

    items.sort(key=_key)


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
            pytest.fail(f"KUBECONFIG is set ({kubeconfig_env}) but could not be loaded: {exc}")
    try:
        config.load_incluster_config()
        return
    except config.ConfigException:
        pass
    try:
        config.load_kube_config()
    except config.ConfigException as exc:
        pytest.fail(f"Cannot load Kubernetes config. Set KUBECONFIG or run inside a cluster. Error: {exc}")


@pytest.fixture(scope="session")
def k8s_core_api(load_kubeconfig) -> client.CoreV1Api:
    return client.CoreV1Api()


@pytest.fixture(scope="session")
def k8s_custom_api(load_kubeconfig) -> client.CustomObjectsApi:
    return client.CustomObjectsApi()


# ---------------------------------------------------------------------------
# Cluster topology
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def amd_gpu_nodes(k8s_core_api: client.CoreV1Api) -> list[client.V1Node]:
    """Return all cluster nodes that carry the AMD GPU NFD label.

    Skips the entire session if no such nodes are found so that individual
    tests do not need to repeat the guard.
    """
    nodes = k8s_core_api.list_node(label_selector=f"{NFD_LABEL_KEY}={NFD_LABEL_VALUE}")
    if not nodes.items:
        pytest.skip("No AMD GPU nodes found in cluster")
    logger.info("Found %d AMD GPU node(s)", len(nodes.items))
    return nodes.items


@pytest.fixture(scope="session")
def initial_gpu_mode(k8s_core_api: client.CoreV1Api) -> str:
    """Detect the starting mode once and initialise _session_mode."""
    pods = k8s_core_api.list_namespaced_pod(NAMESPACE_AMD_GPU).items
    dra_running = any(
        p.metadata.name.startswith(DRA_DRIVER_PREFIX) and p.status.phase == "Running"
        for p in pods
    )
    mode = "dra" if dra_running else "device-plugin"
    _session_mode[0] = mode
    logger.info("Starting GPU mode: %s", mode)
    return mode


# ---------------------------------------------------------------------------
# Mode-switching helpers
# ---------------------------------------------------------------------------


def _switch_to_dra(core_api: client.CoreV1Api, custom_api: client.CustomObjectsApi) -> None:
    logger.info("Switching to DRA mode...")
    _session_mode[0] = "switching"
    patch_device_config(custom_api, {"spec": {
        "devicePlugin": {"enableDevicePlugin": False, "enableNodeLabeller": False},
        "draDriver": {"enable": True},
    }})
    wait_for_pods_gone(core_api, NAMESPACE_AMD_GPU, DEVICE_PLUGIN_PREFIX, timeout=_MODE_SWITCH_TIMEOUT)
    wait_for_pods_gone(core_api, NAMESPACE_AMD_GPU, NODE_LABELLER_PREFIX, timeout=_MODE_SWITCH_TIMEOUT)
    wait_for_pods_running_by_prefix(core_api, NAMESPACE_AMD_GPU, DRA_DRIVER_PREFIX,
                                    min_count=1, timeout=_MODE_SWITCH_TIMEOUT)
    _session_mode[0] = "dra"
    logger.info("Now in DRA mode.")


def _wait_for_gpu_capacity(core_api: client.CoreV1Api, timeout: int = _MODE_SWITCH_TIMEOUT) -> None:
    """Block until all AMD GPU nodes report amd.com/gpu >= 1 in both capacity and allocatable."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        nodes = core_api.list_node(label_selector=f"{NFD_LABEL_KEY}={NFD_LABEL_VALUE}").items
        if nodes and all(
            int((n.status.capacity or {}).get(GPU_RESOURCE_NAME, 0)) >= 1
            and int((n.status.allocatable or {}).get(GPU_RESOURCE_NAME, 0)) >= 1
            for n in nodes
        ):
            logger.info("amd.com/gpu capacity and allocatable registered on all nodes.")
            return
        logger.debug("Waiting for amd.com/gpu capacity+allocatable...")
        time.sleep(10)
    raise TimeoutError(f"amd.com/gpu capacity/allocatable not registered after {timeout}s")


def _switch_to_device_plugin(core_api: client.CoreV1Api, custom_api: client.CustomObjectsApi) -> None:
    logger.info("Switching to device-plugin mode...")
    _session_mode[0] = "switching"
    patch_device_config(custom_api, {"spec": {
        "devicePlugin": {"enableDevicePlugin": True, "enableNodeLabeller": True},
        "draDriver": {"enable": False},
    }})
    wait_for_pods_gone(core_api, NAMESPACE_AMD_GPU, DRA_DRIVER_PREFIX, timeout=_MODE_SWITCH_TIMEOUT)
    wait_for_pods_running_by_prefix(core_api, NAMESPACE_AMD_GPU, DEVICE_PLUGIN_PREFIX,
                                    min_count=1, timeout=_MODE_SWITCH_TIMEOUT)
    wait_for_pods_running_by_prefix(core_api, NAMESPACE_AMD_GPU, NODE_LABELLER_PREFIX,
                                    min_count=1, timeout=_MODE_SWITCH_TIMEOUT)
    _wait_for_gpu_capacity(core_api)
    _session_mode[0] = "device-plugin"
    logger.info("Now in device-plugin mode.")


# ---------------------------------------------------------------------------
# Mode-guard fixtures (switch if needed, never skip)
# ---------------------------------------------------------------------------


def _current_mode(core_api: client.CoreV1Api) -> str:
    """Re-probe the cluster to get the actual current mode."""
    pods = core_api.list_namespaced_pod(NAMESPACE_AMD_GPU).items
    dra_running = any(
        p.metadata.name.startswith(DRA_DRIVER_PREFIX) and p.status.phase == "Running"
        for p in pods
    )
    return "dra" if dra_running else "device-plugin"


@pytest.fixture(scope="class")
def require_dra(
    initial_gpu_mode: str,
    k8s_core_api: client.CoreV1Api,
    k8s_custom_api: client.CustomObjectsApi,
) -> None:
    if _session_mode[0] in ("switching", "device-plugin"):
        _switch_to_dra(k8s_core_api, k8s_custom_api)
    elif _session_mode[0] == "unknown":
        if _current_mode(k8s_core_api) != "dra":
            _switch_to_dra(k8s_core_api, k8s_custom_api)


@pytest.fixture(scope="class")
def require_device_plugin(
    initial_gpu_mode: str,
    k8s_core_api: client.CoreV1Api,
    k8s_custom_api: client.CustomObjectsApi,
) -> None:
    if _session_mode[0] in ("switching", "dra"):
        _switch_to_device_plugin(k8s_core_api, k8s_custom_api)
    elif _session_mode[0] == "unknown":
        if _current_mode(k8s_core_api) != "device-plugin":
            _switch_to_device_plugin(k8s_core_api, k8s_custom_api)
