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
    DRA_DEVICE_CLASS_NAME,
    DRA_DRIVER_NAME,
    DRA_DRIVER_PREFIX,
    DRA_RESOURCE_GROUP,
    DRA_RESOURCE_VERSION,
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
    """Run non-mode tests first, device-plugin tests second, DRA tests last."""
    def _key(item: pytest.Item) -> int:
        names = getattr(item, "fixturenames", [])
        if "require_dra" in names:
            return 2
        if "require_device_plugin" in names:
            return 1
        return 0

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

_DRA_PROBE_CLAIM = "dra-readiness-probe"
_DRA_PROBE_POD = "dra-readiness-probe-pod"
_DRA_PROBE_NS = "default"


def _wait_for_dra_driver_registered(
    core_api: client.CoreV1Api,
    custom_api: client.CustomObjectsApi,
    timeout: int = 480,
) -> None:
    """Block until the DRA driver is registered with the kubelet.

    The driver pod reports Ready before its plugin socket is fully registered
    with the kubelet. We probe by creating a minimal ResourceClaim + pod pair
    and polling until the pod moves past ContainerCreating. If
    FailedPrepareDynamicResources appears, the driver is not yet registered;
    we force-delete the probe pod, recreate it with a fresh claim, and retry.
    Force-deletion (grace_period_seconds=0) is used throughout to avoid
    DRA-finalizer deadlocks on cleanup.
    """
    from kubernetes.client.rest import ApiException
    from tests.amd_gpu.constants import ROCM_TEST_IMAGE

    def _force_delete_pod():
        # Remove DRA finalizers first so the pod isn't stuck in Terminating.
        try:
            core_api.patch_namespaced_pod(
                _DRA_PROBE_POD, _DRA_PROBE_NS, {"metadata": {"finalizers": []}}
            )
        except ApiException:
            pass
        try:
            core_api.delete_namespaced_pod(
                _DRA_PROBE_POD, _DRA_PROBE_NS, grace_period_seconds=0
            )
        except ApiException as e:
            if e.status != 404:
                logger.debug("Probe pod delete: %s", e)

    def _force_delete_claim():
        try:
            custom_api.patch_namespaced_custom_object(
                DRA_RESOURCE_GROUP, DRA_RESOURCE_VERSION,
                _DRA_PROBE_NS, "resourceclaims", _DRA_PROBE_CLAIM,
                {"metadata": {"finalizers": []}},
            )
        except ApiException:
            pass
        try:
            custom_api.delete_namespaced_custom_object(
                DRA_RESOURCE_GROUP, DRA_RESOURCE_VERSION,
                _DRA_PROBE_NS, "resourceclaims", _DRA_PROBE_CLAIM,
            )
        except ApiException as e:
            if e.status != 404:
                logger.debug("Probe claim delete: %s", e)

    def _wait_pod_gone(secs: int = 20) -> None:
        deadline = time.monotonic() + secs
        while time.monotonic() < deadline:
            try:
                core_api.read_namespaced_pod(_DRA_PROBE_POD, _DRA_PROBE_NS)
                time.sleep(2)
            except ApiException as e:
                if e.status == 404:
                    return

    def _wait_claim_gone(secs: int = 20) -> None:
        deadline = time.monotonic() + secs
        while time.monotonic() < deadline:
            try:
                custom_api.get_namespaced_custom_object(
                    DRA_RESOURCE_GROUP, DRA_RESOURCE_VERSION,
                    _DRA_PROBE_NS, "resourceclaims", _DRA_PROBE_CLAIM,
                )
                time.sleep(2)
            except ApiException as e:
                if e.status == 404:
                    return

    def _cleanup_probe():
        _force_delete_pod()
        _force_delete_claim()
        _wait_pod_gone()
        _wait_claim_gone()

    def _create_probe() -> None:
        claim = {
            "apiVersion": f"{DRA_RESOURCE_GROUP}/{DRA_RESOURCE_VERSION}",
            "kind": "ResourceClaim",
            "metadata": {"name": _DRA_PROBE_CLAIM, "namespace": _DRA_PROBE_NS},
            "spec": {"devices": {"requests": [{"name": "gpu", "exactly": {"deviceClassName": DRA_DEVICE_CLASS_NAME}}]}},
        }
        pod = {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": _DRA_PROBE_POD, "namespace": _DRA_PROBE_NS},
            "spec": {
                "restartPolicy": "Never",
                "terminationGracePeriodSeconds": 1,
                "resourceClaims": [{"name": "gpu", "resourceClaimName": _DRA_PROBE_CLAIM}],
                "containers": [{
                    "name": "probe", "image": ROCM_TEST_IMAGE, "imagePullPolicy": "IfNotPresent",
                    "command": ["true"],
                    "resources": {"claims": [{"name": "gpu"}]},
                }],
            },
        }
        custom_api.create_namespaced_custom_object(
            DRA_RESOURCE_GROUP, DRA_RESOURCE_VERSION, _DRA_PROBE_NS, "resourceclaims", claim
        )
        core_api.create_namespaced_pod(_DRA_PROBE_NS, pod)

    # Clean up any leftover probe objects from a previous run.
    _cleanup_probe()

    logger.info("Probing DRA kubelet registration (up to %ds)...", timeout)
    deadline = time.monotonic() + timeout
    attempt = 0
    registered = False

    while time.monotonic() < deadline:
        attempt += 1
        logger.info("DRA registration probe attempt %d", attempt)
        try:
            _create_probe()
        except ApiException as e:
            logger.warning("Could not create probe objects (attempt %d): %s", attempt, e)
            time.sleep(10)
            continue

        attempt_start = time.monotonic()
        while time.monotonic() < deadline:
            try:
                p = core_api.read_namespaced_pod(_DRA_PROBE_POD, _DRA_PROBE_NS)
                phase = p.status.phase
                elapsed = int(time.monotonic() - attempt_start)
                logger.debug("Probe pod phase=%s elapsed=%ds", phase, elapsed)

                if phase in ("Running", "Succeeded", "Failed"):
                    registered = True
                    break

                # Check for FailedPrepareDynamicResources (driver not registered yet).
                evts = core_api.list_namespaced_event(
                    _DRA_PROBE_NS,
                    field_selector=(
                        f"involvedObject.name={_DRA_PROBE_POD}"
                        ",reason=FailedPrepareDynamicResources"
                    ),
                ).items
                if evts:
                    logger.info(
                        "DRA driver not yet registered with kubelet (attempt %d, %ds); "
                        "will recreate probe in 30s",
                        attempt, elapsed,
                    )
                    break

                # After 60s in ContainerCreating with no failure event, the driver
                # is likely mid-initialization. Break and retry with a fresh pod so
                # the kubelet re-attempts preparation once the driver registers.
                if elapsed >= 60 and phase == "Pending":
                    logger.info(
                        "Probe pod stuck in ContainerCreating for %ds (attempt %d); "
                        "recreating probe pod to get fresh kubelet retry",
                        elapsed, attempt,
                    )
                    break

            except ApiException:
                pass
            time.sleep(5)

        if registered:
            break

        # Driver not registered this attempt — clean up and wait before retry.
        _cleanup_probe()
        if time.monotonic() < deadline:
            time.sleep(30)

    # Final cleanup regardless of outcome.
    try:
        _cleanup_probe()
    except Exception:
        pass

    if not registered:
        raise TimeoutError(
            f"DRA driver did not register with the kubelet within {timeout}s "
            f"({attempt} probe attempt(s))"
        )
    logger.info("DRA driver registered with kubelet (attempt %d).", attempt)


_DRA_MIN_STABLE_SECONDS = 90


def _wait_for_dra_resource_slice(
    core_api: client.CoreV1Api,
    custom_api: client.CustomObjectsApi,
    timeout: int = 720,
) -> None:
    """Block until the DRA driver pod has published a stable, fresh ResourceSlice.

    The AMD GPU operator fires multiple rapid reconciles right after a DeviceConfig
    change, causing 1-2 DaemonSet rolling updates in quick succession.  Each update
    kills the current driver pod and creates a new one.  We must wait for the FINAL
    stable pod — not an intermediate one that will be replaced seconds later.

    Strategy:
    1. Re-fetch the current running DRA driver pod on every poll iteration so we
       always track the most-recently started pod.
    2. Filter ResourceSlices by requiring creationTimestamp ≥ current pod's startTime
       (ignores stale slices from earlier sessions).
    3. Once a fresh ResourceSlice is found, enforce a stability window: the same pod
       must remain running for at least _DRA_MIN_STABLE_SECONDS.  If the pod changes
       during the window (operator did another rolling update), restart from step 1.
    """
    import datetime

    logger.info(
        "Waiting for stable DRA ResourceSlice (up to %ds, stability=%ds)...",
        timeout, _DRA_MIN_STABLE_SECONDS,
    )

    def _get_current_driver_pod():
        """Return (name, start_time) of the most recently started running DRA pod."""
        pods = core_api.list_namespaced_pod(NAMESPACE_AMD_GPU).items
        running = [
            p for p in pods
            if p.metadata.name.startswith(DRA_DRIVER_PREFIX) and p.status.phase == "Running"
            and p.status.start_time is not None
        ]
        if not running:
            return None, None
        running.sort(key=lambda p: p.status.start_time)
        newest = running[-1]
        return newest.metadata.name, newest.status.start_time

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        pod_name, pod_start = _get_current_driver_pod()
        if pod_name is None:
            logger.debug("No running DRA driver pod yet; waiting...")
            time.sleep(10)
            continue

        # Check for a ResourceSlice that belongs to this pod (not a stale one).
        try:
            result = custom_api.list_cluster_custom_object(
                DRA_RESOURCE_GROUP, DRA_RESOURCE_VERSION, "resourceslices"
            )
            fresh_slices = []
            pod_start_tz = pod_start.replace(tzinfo=datetime.timezone.utc)
            for item in result.get("items", []):
                if item.get("spec", {}).get("driver") != DRA_DRIVER_NAME:
                    continue
                if not item.get("spec", {}).get("devices"):
                    continue
                created_str = item.get("metadata", {}).get("creationTimestamp", "")
                try:
                    created = datetime.datetime.fromisoformat(
                        created_str.replace("Z", "+00:00")
                    )
                    if created < pod_start_tz:
                        logger.debug(
                            "Ignoring stale ResourceSlice %s (created %s, pod %s started %s)",
                            item["metadata"]["name"], created_str, pod_name, pod_start,
                        )
                        continue
                except Exception:
                    pass
                fresh_slices.append(item)

            if not fresh_slices:
                logger.debug("No fresh ResourceSlice from pod %s yet; waiting...", pod_name)
                time.sleep(10)
                continue

            total_devices = sum(len(s["spec"]["devices"]) for s in fresh_slices)
            logger.info(
                "Fresh ResourceSlice from pod %s: %d device(s) across %d slice(s). "
                "Waiting %ds for driver to stabilise...",
                pod_name, total_devices, len(fresh_slices), _DRA_MIN_STABLE_SECONDS,
            )

            # Stability window: keep the same pod running for _DRA_MIN_STABLE_SECONDS.
            # If the operator rolls it out again, restart the whole search.
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            elapsed = (now_utc - pod_start_tz).total_seconds()
            remaining_stable = max(0.0, _DRA_MIN_STABLE_SECONDS - elapsed)

            if remaining_stable > 0:
                logger.info(
                    "Pod %s has been running %.0fs; waiting %.0fs more for stability.",
                    pod_name, elapsed, remaining_stable,
                )
                time.sleep(remaining_stable)

            # Re-verify: same pod still running?
            current_name, _ = _get_current_driver_pod()
            if current_name != pod_name:
                logger.info(
                    "DRA driver pod changed (%s → %s) during stability window; re-polling.",
                    pod_name, current_name,
                )
                time.sleep(5)
                continue

            logger.info(
                "DRA driver pod %s is stable with %d device(s). Ready.",
                pod_name, total_devices,
            )
            return

        except Exception:
            pass

        time.sleep(10)

    raise TimeoutError(
        f"DRA driver did not publish a stable ResourceSlice within {timeout}s"
    )


def _switch_to_dra(core_api: client.CoreV1Api, custom_api: client.CustomObjectsApi) -> None:
    logger.info("Switching to DRA mode...")
    _session_mode[0] = "switching"
    patch_device_config(custom_api, {"spec": {
        "devicePlugin": {"enableDevicePlugin": False, "enableNodeLabeller": False},
        "draDriver": {"enable": True},
    }})
    wait_for_pods_gone(core_api, NAMESPACE_AMD_GPU, DEVICE_PLUGIN_PREFIX, timeout=_MODE_SWITCH_TIMEOUT)
    wait_for_pods_gone(core_api, NAMESPACE_AMD_GPU, NODE_LABELLER_PREFIX, timeout=_MODE_SWITCH_TIMEOUT)
    # The operator fires multiple rapid reconciles after the DeviceConfig patch,
    # causing 1-2 DaemonSet rolling updates that kill intermediate driver pods.
    # _wait_for_dra_resource_slice handles this: it re-tracks the current pod on
    # each iteration and enforces a stability window before returning.
    _wait_for_dra_resource_slice(core_api, custom_api)
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


def _operator_supports_dra(custom_api: client.CustomObjectsApi) -> bool:
    """Return True if the installed operator accepts the draDriver field.

    Reads the current DeviceConfig spec and checks whether the draDriver field
    is present. Operators older than v1.5 treat draDriver as an unknown field
    and silently drop it, so the field will be absent from the stored spec.
    """
    try:
        dc = custom_api.get_namespaced_custom_object(
            DEVICECONFIG_GROUP, DEVICECONFIG_VERSION,
            NAMESPACE_AMD_GPU, DEVICECONFIG_PLURAL, DEVICECONFIG_NAME,
        )
        return "draDriver" in (dc.get("spec") or {})
    except Exception:
        return False


def _dra_enabled_in_deviceconfig(custom_api: client.CustomObjectsApi) -> bool:
    """Return True if the DeviceConfig has draDriver.enable=True."""
    try:
        dc = custom_api.get_namespaced_custom_object(
            DEVICECONFIG_GROUP, DEVICECONFIG_VERSION,
            NAMESPACE_AMD_GPU, DEVICECONFIG_PLURAL, DEVICECONFIG_NAME,
        )
        return bool((dc.get("spec") or {}).get("draDriver", {}).get("enable", False))
    except Exception:
        return False


@pytest.fixture(scope="class")
def require_dra(
    initial_gpu_mode: str,
    k8s_core_api: client.CoreV1Api,
    k8s_custom_api: client.CustomObjectsApi,
) -> None:
    if not _operator_supports_dra(k8s_custom_api):
        pytest.skip("DRA not supported by this operator version (draDriver field not accepted)")
    if _session_mode[0] in ("switching", "device-plugin"):
        _switch_to_dra(k8s_core_api, k8s_custom_api)
    elif _session_mode[0] == "unknown":
        if _current_mode(k8s_core_api) != "dra":
            _switch_to_dra(k8s_core_api, k8s_custom_api)
    elif _session_mode[0] == "dra" and not _dra_enabled_in_deviceconfig(k8s_custom_api):
        # _session_mode says dra (stale pod found at session start) but the
        # DeviceConfig has draDriver.enable=False — re-enable to get a live driver.
        logger.info("_session_mode=dra but draDriver.enable=False in DeviceConfig; switching.")
        _switch_to_dra(k8s_core_api, k8s_custom_api)


def _device_plugin_running(core_api: client.CoreV1Api) -> bool:
    """Return True if at least one device-plugin pod is currently Running."""
    pods = core_api.list_namespaced_pod(NAMESPACE_AMD_GPU).items
    return any(
        p.metadata.name.startswith(DEVICE_PLUGIN_PREFIX) and p.status.phase == "Running"
        for p in pods
    )


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
    elif _session_mode[0] == "device-plugin" and not _device_plugin_running(k8s_core_api):
        # _session_mode says device-plugin but no pods are actually running (e.g. the
        # cluster was left in a half-restored state from a previous session).
        logger.info("_session_mode=device-plugin but no device-plugin pods running; switching.")
        _switch_to_device_plugin(k8s_core_api, k8s_custom_api)
