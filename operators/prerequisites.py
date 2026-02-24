"""
Verify prerequisites and configure internal registry per AMD GPU Operator doc.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from operators.errors import OperatorError

from operators.constants import (
    REGISTRY_NAMESPACE,
    REGISTRY_PATCH_MANAGED,
    REGISTRY_PATCH_STORAGE,
)

if TYPE_CHECKING:
    from operators.oc_runner import OcRunner


# Required operators (grep pattern -> description)
REQUIRED_OPERATOR_PATTERNS = [
    ("service-ca", "Service CA Operator"),
    ("operator-lifecycle", "Operator Lifecycle Manager (OLM)"),
    ("machine-config", "MachineConfig Operator"),
    ("image-registry", "Cluster Image Registry Operator"),
]


def verify_required_operators(oc: OcRunner, timeout: int = 300) -> None:
    """
    Verify required operators have running pods (all namespaces).
    Doc: Service CA, OLM, MachineConfig, Cluster Image Registry.
    """
    print("Verifying required cluster operators...")
    start = time.monotonic()
    while True:
        if time.monotonic() - start > timeout:
            raise OperatorError(
                f"Timeout ({timeout}s) waiting for required operators to be ready"
            )
        all_ok = True
        r = oc.oc("get", "pods", "-A", "--no-headers", timeout=30)
        if r.returncode != 0:
            elapsed = int(time.monotonic() - start)
            print(f"  API not reachable yet ({elapsed}s)...")
            all_ok = False
        else:
            for pattern, name in REQUIRED_OPERATOR_PATTERNS:
                lines = [line for line in (r.stdout or "").splitlines() if pattern in line and "Running" in line]
                if not lines:
                    print(f"  Waiting for {name} ({pattern})...")
                    all_ok = False
                    break
        if all_ok:
            print("  All required operators are running.")
            return
        time.sleep(15)


def configure_internal_registry(oc: OcRunner, timeout: int = 120) -> None:
    """
    Enable and configure OpenShift internal image registry per doc:
    - Patch storage (emptyDir example)
    - Set managementState to Managed
    - Verify registry pod is running
    """
    print("Configuring OpenShift internal image registry...")

    r = oc.oc(
        "patch",
        "configs.imageregistry.operator.openshift.io",
        "cluster",
        "--type=merge",
        f"--patch={REGISTRY_PATCH_STORAGE}",
        timeout=30,
    )
    if r.returncode != 0:
        raise OperatorError(f"Failed to patch registry storage: {r.stderr or r.stdout}")

    r = oc.oc(
        "patch",
        "configs.imageregistry.operator.openshift.io",
        "cluster",
        "--type=merge",
        f"--patch={REGISTRY_PATCH_MANAGED}",
        timeout=30,
    )
    if r.returncode != 0:
        raise OperatorError(
            f"Failed to set registry managementState: {r.stderr or r.stdout}"
        )

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc("get", "pods", "-n", REGISTRY_NAMESPACE, "--no-headers", timeout=30)
        if r.returncode == 0 and "Running" in (r.stdout or ""):
            print("  Internal registry is running.")
            return
        time.sleep(10)

    raise OperatorError(
        f"Timeout ({timeout}s) waiting for registry pod in {REGISTRY_NAMESPACE}"
    )
