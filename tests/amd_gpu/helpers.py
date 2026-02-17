"""Helper utilities for AMD GPU operator verification tests."""

from __future__ import annotations

import logging
import time

from kubernetes import client
from kubernetes.client.rest import ApiException

from tests.amd_gpu.constants import (
    GPU_RESOURCE_NAME,
    NAMESPACE_AMD_GPU,
    POD_COMPLETION_POLL_INTERVAL,
    POD_COMPLETION_TIMEOUT,
    POD_DELETION_POLL_INTERVAL,
    POD_DELETION_TIMEOUT,
    ROCM_TEST_IMAGE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pod lifecycle helpers
# ---------------------------------------------------------------------------


def delete_pod_if_exists(
    core_api: client.CoreV1Api,
    name: str,
    namespace: str,
) -> None:
    """Delete a pod and block until it is gone."""
    try:
        core_api.delete_namespaced_pod(name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            return
        raise

    deadline = time.monotonic() + POD_DELETION_TIMEOUT
    while time.monotonic() < deadline:
        try:
            core_api.read_namespaced_pod(name, namespace)
            time.sleep(POD_DELETION_POLL_INTERVAL)
        except ApiException as exc:
            if exc.status == 404:
                return
            raise
    raise TimeoutError(
        f"Pod {namespace}/{name} was not deleted within {POD_DELETION_TIMEOUT}s"
    )


def wait_for_pod_done(
    core_api: client.CoreV1Api,
    name: str,
    namespace: str,
    timeout: int,
) -> str:
    """Wait for a pod to reach Succeeded or Failed and return the phase.

    Raises early with a descriptive message if the pod is stuck due to
    image-pull errors, scheduling failures, or other non-transient issues
    instead of waiting for the full timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pod = core_api.read_namespaced_pod(name, namespace)
        phase = pod.status.phase
        if phase in ("Succeeded", "Failed"):
            return phase

        # Surface actionable errors instead of spinning until timeout.
        if phase == "Pending":
            check_pending_pod_errors(pod, name, namespace)

        time.sleep(POD_COMPLETION_POLL_INTERVAL)

    # On timeout, include current pod status for debugging.
    pod = core_api.read_namespaced_pod(name, namespace)
    status_detail = describe_pod_status(pod)
    raise TimeoutError(
        f"Pod {namespace}/{name} did not complete within {timeout}s. "
        f"Current phase: {pod.status.phase}. {status_detail}"
    )


FATAL_WAITING_REASONS = frozenset({
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
})


def check_pending_pod_errors(
    pod: client.V1Pod,
    name: str,
    namespace: str,
) -> None:
    """Raise immediately if the pod is stuck for a non-transient reason."""
    # Check container waiting state for image / config errors.
    for cs in pod.status.container_statuses or []:
        waiting = cs.state and cs.state.waiting
        if waiting and waiting.reason in FATAL_WAITING_REASONS:
            raise RuntimeError(
                f"Pod {namespace}/{name} cannot start: "
                f"{waiting.reason} — {waiting.message}"
            )

    # Check pod conditions for scheduling failures (Unschedulable).
    for cond in pod.status.conditions or []:
        if (
            cond.type == "PodScheduled"
            and cond.status == "False"
            and cond.reason == "Unschedulable"
        ):
            raise RuntimeError(
                f"Pod {namespace}/{name} cannot be scheduled: "
                f"{cond.message}"
            )


def describe_pod_status(pod: client.V1Pod) -> str:
    """Return a short human-readable summary of a pod's current status."""
    parts: list[str] = []
    for cs in pod.status.container_statuses or []:
        waiting = cs.state and cs.state.waiting
        if waiting:
            parts.append(f"container '{cs.name}': {waiting.reason} — {waiting.message}")
    for cond in pod.status.conditions or []:
        if cond.status == "False":
            parts.append(f"condition {cond.type}: {cond.reason} — {cond.message}")
    return "; ".join(parts) if parts else "no additional detail available"


# ---------------------------------------------------------------------------
# GPU command runner
# ---------------------------------------------------------------------------


def run_gpu_command(
    core_api: client.CoreV1Api,
    pod_name: str,
    command: list[str],
    *,
    namespace: str = NAMESPACE_AMD_GPU,
    image: str = ROCM_TEST_IMAGE,
    gpu_count: str = "1",
    timeout: int = POD_COMPLETION_TIMEOUT,
) -> str:
    """Create a privileged pod with a GPU, run *command*, and return its logs.

    The pod requests ``amd.com/gpu`` resources, executes the given command,
    and is always cleaned up regardless of success or failure.

    Args:
        core_api: Kubernetes CoreV1Api client.
        pod_name: Name for the ephemeral test pod.
        command: Entrypoint command for the container.
        namespace: Target namespace (default: ``openshift-amd-gpu``).
        image: Container image (default: ``rocm/rocm-terminal:latest``).
        gpu_count: Number of GPUs to request as a string.
        timeout: Seconds to wait for the pod to finish.

    Returns:
        The container log output.

    Raises:
        AssertionError: If the pod exits with a Failed phase.
        TimeoutError: If the pod does not finish in time.
    """
    delete_pod_if_exists(core_api, pod_name, namespace)

    pod_body = client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name, namespace=namespace),
        spec=client.V1PodSpec(
            restart_policy="Never",
            termination_grace_period_seconds=1,
            containers=[
                client.V1Container(
                    name=pod_name,
                    image=image,
                    command=command,
                    resources=client.V1ResourceRequirements(
                        requests={GPU_RESOURCE_NAME: gpu_count},
                        limits={GPU_RESOURCE_NAME: gpu_count},
                    ),
                    security_context=client.V1SecurityContext(
                        privileged=True,
                        allow_privilege_escalation=True,
                    ),
                ),
            ],
        ),
    )

    try:
        core_api.create_namespaced_pod(namespace, pod_body)
        logger.info("Created pod %s/%s", namespace, pod_name)

        phase = wait_for_pod_done(core_api, pod_name, namespace, timeout)
        logs = core_api.read_namespaced_pod_log(pod_name, namespace)
        logger.info("Pod %s finished with phase %s", pod_name, phase)

        assert phase == "Succeeded", (
            f"Pod {pod_name} failed (phase={phase}). Logs:\n{logs}"
        )
        return logs
    finally:
        delete_pod_if_exists(core_api, pod_name, namespace)
        logger.info("Cleaned up pod %s/%s", namespace, pod_name)
