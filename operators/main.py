"""
Orchestrate AMD GPU Operator and dependencies installation per AMD OpenShift OLM doc.

Steps (in order, matching eco-gotests BeforeAll and AMD docs):
1. Verify prerequisites (Service CA, OLM, MachineConfig, Image Registry operators)
2. Configure internal image registry
3. Wait for cluster stability
4. Create amdgpu blacklist MachineConfig (BEFORE operators to avoid disrupting them on reboot)
5. Wait for MachineConfigPool to finish updating (handles node reboot)
6. Wait for cluster stability after reboot
7. Install NFD, KMM, AMD GPU Operator via OLM
8. Create NodeFeatureDiscovery instance (starts NFD pods)
9. Create NodeFeatureRule (separate CR for AMD GPU detection, per docs)
10. Wait for NFD to label nodes
11. Create DeviceConfig
12. Enable cluster monitoring
13. Wait for cluster stability
14. Wait for GPU resources to become available
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from operators.config import (
    create_amdgpu_blacklist,
    create_device_config,
    create_nfd_feature_rule,
    create_nfd_instance,
    enable_cluster_monitoring,
)
from operators.constants import NAMESPACE_AMD_GPU
from operators.install import install_all_operators, wait_for_device_config_crd
from operators.prerequisites import configure_internal_registry, verify_required_operators

if TYPE_CHECKING:
    from operators.oc_runner import OcRunner


@dataclass
class OperatorInstallConfig:
    """Options for operator installation and configuration."""

    # MachineConfig role: "master" for SNO, "worker" for multi-node
    machine_config_role: str = "worker"
    # Full AMD GPU Operator version to install (e.g. "1.4.1"); used as startingCSV
    gpu_operator_version: str = "1.4.1"
    # ROCm/amdgpu driver version (e.g. 30.20.1)
    driver_version: str = "30.20.1"
    # Enable metrics exporter and ServiceMonitor
    enable_metrics: bool = True
    # OCP version for NFD operand image (4.16 requires explicit image)
    ocp_version: str | None = None
    # Timeouts (seconds)
    prerequisite_timeout: int = 900
    registry_timeout: int = 120
    operator_timeout: int = 600
    cluster_stability_timeout: int = 900
    gpu_ready_timeout: int = 1800


def wait_for_cluster_stability(
    oc: OcRunner,
    timeout: int = 900,
    poll_interval: int = 20,
) -> None:
    """Wait for all nodes to be Ready and all ClusterOperators to be healthy.

    Checks:
    - Every node has condition ``Ready=True``
    - Every ClusterOperator has ``Available=True``, ``Progressing=False``,
      ``Degraded=False``

    Tolerates temporary API unavailability (e.g. during node reboots on
    SNO clusters).
    """
    print("Waiting for cluster stability...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        elapsed = int(timeout - (deadline - time.monotonic()))
        issues: list[str] = []

        # Check nodes.
        r = oc.oc(
            "get", "nodes", "--no-headers",
            "-o", "custom-columns="
            "NAME:.metadata.name,"
            "READY:.status.conditions[?(@.type==\"Ready\")].status",
            timeout=15,
        )
        if r.returncode != 0:
            print(f"  API not reachable ({elapsed}s)...")
            time.sleep(poll_interval)
            continue
        for line in (r.stdout or "").strip().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] != "True":
                issues.append(f"node '{parts[0]}' not Ready")

        # Check ClusterOperators.
        r = oc.oc(
            "get", "clusteroperators", "--no-headers",
            "-o", "custom-columns="
            "NAME:.metadata.name,"
            "AVAILABLE:.status.conditions[?(@.type==\"Available\")].status,"
            "PROGRESSING:.status.conditions[?(@.type==\"Progressing\")].status,"
            "DEGRADED:.status.conditions[?(@.type==\"Degraded\")].status",
            timeout=15,
        )
        if r.returncode != 0:
            print(f"  Cannot check ClusterOperators ({elapsed}s)...")
            time.sleep(poll_interval)
            continue
        for line in (r.stdout or "").strip().splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            name, available, progressing, degraded = (
                parts[0], parts[1], parts[2], parts[3]
            )
            if available != "True":
                issues.append(f"CO '{name}' not Available")
            if progressing == "True":
                issues.append(f"CO '{name}' still Progressing")
            if degraded == "True":
                issues.append(f"CO '{name}' is Degraded")

        if not issues:
            print("  Cluster is stable (all nodes Ready, all ClusterOperators healthy).")
            return

        summary = "; ".join(issues[:3])
        if len(issues) > 3:
            summary += f" (+{len(issues) - 3} more)"
        print(f"  {summary} ({elapsed}s)...")
        time.sleep(poll_interval)

    raise RuntimeError(
        f"Cluster did not stabilize within {timeout}s. "
        "Check node status and ClusterOperator conditions."
    )


def wait_for_gpu_ready(
    oc: OcRunner,
    timeout: int = 900,
    poll_interval: int = 30,
) -> None:
    """Wait for the AMD GPU operator to fully deploy.

    After the DeviceConfig is created, the operator must:
    1. Build the GPU driver kernel module (KMM) — can take several minutes.
    2. Deploy node-labeller pods that label nodes with GPU metadata.
    3. Deploy device-plugin pods that register ``amd.com/gpu`` resources.

    This function polls until at least one node reports ``amd.com/gpu``
    capacity >= 1.
    """
    print("Waiting for AMD GPU resources to become available...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        elapsed = int(timeout - (deadline - time.monotonic()))

        # Check if device-plugin pods are running.
        r = oc.oc(
            "get", "pods", "-n", NAMESPACE_AMD_GPU,
            "--no-headers", timeout=30,
        )
        dp_pods = []
        if r.returncode == 0:
            dp_pods = [
                line for line in (r.stdout or "").splitlines()
                if "device-plugin" in line and "Running" in line
            ]

        # Check if any node reports amd.com/gpu capacity.
        r = oc.oc(
            "get", "nodes",
            "-o", "jsonpath={.items[*].status.capacity.amd\\.com/gpu}",
            timeout=30,
        )
        gpu_counts = []
        if r.returncode == 0 and r.stdout and r.stdout.strip():
            gpu_counts = [int(x) for x in r.stdout.strip().split() if x.isdigit()]

        total_gpus = sum(gpu_counts)
        if dp_pods and total_gpus >= 1:
            print(
                f"  GPU resources ready: {len(dp_pods)} device-plugin pod(s), "
                f"{total_gpus} GPU(s) available."
            )
            return

        # Print progress.
        status_parts = []
        if not dp_pods:
            status_parts.append("no device-plugin pods yet")
        else:
            status_parts.append(f"{len(dp_pods)} device-plugin pod(s)")
        status_parts.append(f"GPU capacity: {total_gpus}")
        print(f"  {', '.join(status_parts)} ({elapsed}s)...")
        time.sleep(poll_interval)

    raise RuntimeError(
        f"AMD GPU resources did not become available within {timeout}s. "
        "Check KMM build pods and operator logs."
    )


def wait_for_mcp_updated(
    oc: OcRunner,
    timeout: int = 900,
    poll_interval: int = 20,
) -> None:
    """Wait for MachineConfigPool to finish updating after a MachineConfig change.

    MachineConfigOperator reboots nodes when a MachineConfig is applied.
    On SNO clusters the single node hosts the API server, so the API goes
    down during the reboot.  Simply checking if the API is up is racy
    because the reboot may not have started yet.

    Instead, this function waits for the MachineConfigPool to report
    ``UPDATED=True`` and ``UPDATING=False``, which means MCO has finished
    applying the config and all reboots are complete.  While the API is
    down (during reboot), we catch failures and keep polling.
    """
    print("Waiting for MachineConfigPool to finish updating...")
    deadline = time.monotonic() + timeout
    saw_updating = False
    while time.monotonic() < deadline:
        elapsed = int(timeout - (deadline - time.monotonic()))
        r = oc.oc(
            "get", "mcp", "--no-headers",
            "-o", "custom-columns="
            "NAME:.metadata.name,"
            "UPDATED:.status.conditions[?(@.type==\"Updated\")].status,"
            "UPDATING:.status.conditions[?(@.type==\"Updating\")].status,"
            "DEGRADED:.status.conditions[?(@.type==\"Degraded\")].status",
            timeout=15,
        )
        if r.returncode != 0:
            # API is down (node is rebooting).
            saw_updating = True
            print(f"  API not reachable (node likely rebooting) ({elapsed}s)...")
            time.sleep(poll_interval)
            continue

        # Parse MCP status lines.
        all_updated = True
        for line in (r.stdout or "").strip().splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            name, updated, updating, degraded = parts[0], parts[1], parts[2], parts[3]
            if updating == "True":
                saw_updating = True
                all_updated = False
                print(f"  MCP '{name}' is still updating ({elapsed}s)...")
            elif updated != "True":
                all_updated = False
                print(f"  MCP '{name}' not yet updated ({elapsed}s)...")

        if all_updated and (r.stdout or "").strip():
            if saw_updating:
                print("  All MachineConfigPools updated, reboot complete.")
            else:
                # MCO hasn't started updating yet — wait a bit longer.
                if elapsed < 60:
                    print(f"  MCP shows updated but MCO may not have started yet ({elapsed}s)...")
                    time.sleep(poll_interval)
                    continue
                print("  All MachineConfigPools updated (MCO may have been fast).")
            return

        time.sleep(poll_interval)

    raise RuntimeError(
        f"MachineConfigPool did not finish updating within {timeout}s"
    )


def install_operators(
    oc: OcRunner,
    config: OperatorInstallConfig | None = None,
) -> None:
    """Run full AMD GPU Operator installation flow per AMD docs and
    eco-gotests ordering.

    1. Verify required operators (Service CA, OLM, MCO, Image Registry)
    2. Configure internal registry (storage, Managed)
    3. Wait for cluster stability
    4. Create MachineConfig (amdgpu blacklist) — BEFORE operators to
       avoid disrupting operator pods during the MCO node reboot
    5. Wait for MachineConfigPool to finish updating
    6. Wait for cluster stability after reboot
    7. Install NFD, KMM, AMD GPU Operator via OLM
    8. Create NodeFeatureDiscovery instance (starts NFD pods)
    9. Create NodeFeatureRule (separate CR for AMD GPU detection)
    10. Wait for NFD to label nodes
    11. Create DeviceConfig
    12. Enable cluster monitoring
    13. Wait for cluster stability
    14. Wait for GPU resources (device-plugin pods + amd.com/gpu capacity)
    """
    cfg = config or OperatorInstallConfig()
    print("\n" + "=" * 60)
    print("AMD GPU Operator & Dependencies Installation (OLM)")
    print("=" * 60)

    verify_required_operators(oc, timeout=cfg.prerequisite_timeout)
    configure_internal_registry(oc, timeout=cfg.registry_timeout)

    wait_for_cluster_stability(oc, timeout=cfg.cluster_stability_timeout)

    # MachineConfig BEFORE operators — reboot would kill operator pods.
    create_amdgpu_blacklist(oc, role=cfg.machine_config_role)
    wait_for_mcp_updated(oc)
    wait_for_cluster_stability(oc, timeout=cfg.cluster_stability_timeout)

    install_all_operators(
        oc,
        gpu_operator_version=cfg.gpu_operator_version,
        timeout_per_operator=cfg.operator_timeout,
    )

    create_nfd_instance(oc, ocp_version=cfg.ocp_version)
    create_nfd_feature_rule(oc)

    print("Waiting for NFD to label nodes (60s)...")
    time.sleep(60)

    print("Waiting for DeviceConfig CRD (AMD GPU Operator)...")
    api_version = wait_for_device_config_crd(oc, timeout=180)
    create_device_config(
        oc,
        driver_version=cfg.driver_version,
        enable_metrics=cfg.enable_metrics,
        api_version=api_version,
    )
    enable_cluster_monitoring(oc)

    wait_for_cluster_stability(oc, timeout=cfg.cluster_stability_timeout)
    wait_for_gpu_ready(oc, timeout=cfg.gpu_ready_timeout)

    print("\n" + "=" * 60)
    print("AMD GPU Operator installation completed successfully.")
    print("=" * 60)
