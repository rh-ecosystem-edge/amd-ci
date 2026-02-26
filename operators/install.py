"""
Install NFD, KMM, and AMD GPU Operator via OLM (Subscription).
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from operators.errors import OperatorError

from operators.constants import (
    AMD_GPU_CATALOG,
    AMD_GPU_CHANNEL,
    AMD_GPU_PACKAGE,
    DEVICECONFIG_CRD_NAME,
    KMM_CATALOG,
    KMM_CHANNEL,
    KMM_PACKAGE,
    KMM_STARTING_CSV,
    NAMESPACE_AMD_GPU,
    NAMESPACE_KMM,
    NAMESPACE_NFD,
    NFD_CATALOG,
    NFD_CHANNEL,
    NFD_PACKAGE,
)

if TYPE_CHECKING:
    from operators.oc_runner import OcRunner


def ensure_namespace(oc: OcRunner, name: str) -> None:
    r = oc.oc("get", "namespace", name, timeout=10)
    if r.returncode == 0:
        return
    r = oc.oc("create", "namespace", name, timeout=10)
    if r.returncode != 0:
        raise OperatorError(f"Failed to create namespace {name}: {r.stderr or r.stdout}")


def create_operator_group(
    oc: OcRunner,
    namespace: str,
    name: str,
    all_namespaces: bool = False,
) -> None:
    """Create an OperatorGroup. Use all_namespaces=True for operators that only support AllNamespaces (e.g. KMM)."""
    if all_namespaces:
        spec_block = "spec: {}"
    else:
        spec_block = f"""spec:
  targetNamespaces:
  - {namespace}"""
    yaml = f"""apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: {name}
  namespace: {namespace}
{spec_block}
"""
    oc.apply_yaml(yaml)


def create_subscription(
    oc: OcRunner,
    namespace: str,
    name: str,
    package: str,
    catalog: str,
    channel: str,
    starting_csv: str | None = None,
) -> None:
    starting_csv_block = ""
    if starting_csv:
        starting_csv_block = f"\n  startingCSV: {starting_csv}"
    yaml = f"""apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: {name}
  namespace: {namespace}
spec:
  channel: {channel}
  installPlanApproval: Automatic
  name: {package}
  source: {catalog}
  sourceNamespace: openshift-marketplace
{starting_csv_block}
"""
    oc.apply_yaml(yaml)


def wait_for_csv(oc: OcRunner, namespace: str, timeout: int = 600) -> None:
    """Wait for any installing CSV in namespace to reach Succeeded."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc(
            "get", "csv", "-n", namespace, "-o", "jsonpath={.items[*].status.phase}",
            timeout=30,
        )
        if r.returncode != 0:
            time.sleep(15)
            continue
        phases = (r.stdout or "").split()
        if not phases:
            time.sleep(15)
            continue
        if all(p == "Succeeded" for p in phases):
            return
        if "Failed" in phases:
            r2 = oc.oc("get", "csv", "-n", namespace, "-o", "yaml", timeout=10)
            raise OperatorError(
                f"CSV in {namespace} failed: {r2.stdout or 'check oc get csv -n ' + namespace}"
            )
        print(f"  Waiting for operator CSV in {namespace}... ({phases})")
        time.sleep(15)
    raise OperatorError(f"Timeout ({timeout}s) waiting for CSV in {namespace}")


def wait_for_subscription_installed(
    oc: OcRunner, namespace: str, subscription_name: str, timeout: int = 600
) -> str:
    """
    Wait for subscription to have status.installedCSV set, or raise if ResolutionFailed.
    Returns the installedCSV name.
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc(
            "get", "subscription", subscription_name, "-n", namespace, "-o", "json",
            timeout=15,
        )
        if r.returncode != 0:
            time.sleep(10)
            continue
        try:
            sub = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            time.sleep(10)
            continue
        conditions = sub.get("status", {}).get("conditions") or []
        for c in conditions:
            if c.get("type") == "ResolutionFailed" and c.get("status") == "True":
                msg = c.get("message") or "Subscription resolution failed."
                raise OperatorError(
                    f"AMD GPU Operator subscription failed: {msg} "
                    "Check that package amd-gpu-operator exists in the certified-operators catalog and channel."
                )
        installed = (sub.get("status") or {}).get("installedCSV", "").strip()
        if installed:
            return installed
        print(f"  Waiting for subscription {subscription_name} to resolve...")
        time.sleep(10)
    raise OperatorError(
        f"Timeout ({timeout}s) waiting for subscription {subscription_name} to install (no installedCSV)."
    )


def wait_for_csv_by_name(
    oc: OcRunner, namespace: str, csv_name: str, timeout: int = 600
) -> None:
    """Wait for a specific CSV to reach Succeeded."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc(
            "get", "csv", csv_name, "-n", namespace,
            "-o", "jsonpath={.status.phase}",
            timeout=10,
        )
        if r.returncode == 0:
            phase = (r.stdout or "").strip()
            if phase == "Succeeded":
                return
            if phase == "Failed":
                r2 = oc.oc("get", "csv", csv_name, "-n", namespace, "-o", "yaml", timeout=10)
                raise OperatorError(f"CSV {csv_name} failed: {r2.stdout or 'check oc get csv'}")
        time.sleep(10)
    raise OperatorError(f"Timeout ({timeout}s) waiting for CSV {csv_name} to reach Succeeded.")


def list_amd_crds(oc: OcRunner) -> list[str]:
    """Return CRD names that contain 'amd' (for diagnostics when DeviceConfig CRD wait times out)."""
    r = oc.oc("get", "crd", "-o", "jsonpath={.items[*].metadata.name}", timeout=15)
    if r.returncode != 0:
        return []
    return [n for n in (r.stdout or "").split() if "amd" in n.lower()]


def get_amd_gpu_csv_item(oc: OcRunner) -> dict | None:
    """
    Return the AMD GPU Operator CSV (Succeeded) in openshift-amd-gpu.
    Prefer the one named by the subscription's installedCSV; otherwise the first Succeeded CSV
    that owns a DeviceConfig CRD. KMM also deploys a CSV here (AllNamespaces), so we must not use it.
    """
    # Prefer subscription's installedCSV so we get the actual AMD GPU operator CSV
    r = oc.oc(
        "get", "subscription", "amd-gpu-operator", "-n", NAMESPACE_AMD_GPU,
        "-o", "jsonpath={.status.installedCSV}",
        timeout=10,
    )
    installed_csv = (r.stdout or "").strip() if r.returncode == 0 else ""
    if installed_csv:
        r2 = oc.oc("get", "csv", installed_csv, "-n", NAMESPACE_AMD_GPU, "-o", "json", timeout=10)
        if r2.returncode == 0 and r2.stdout:
            try:
                csv_item = json.loads(r2.stdout)
                if (csv_item.get("status") or {}).get("phase") == "Succeeded":
                    return csv_item
            except json.JSONDecodeError:
                pass
    # Fallback: find any Succeeded CSV that owns DeviceConfig (so we skip KMM)
    r = oc.oc("get", "csv", "-n", NAMESPACE_AMD_GPU, "-o", "json", timeout=15)
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    for csv_item in data.get("items") or []:
        if (csv_item.get("status") or {}).get("phase") != "Succeeded":
            continue
        owned = (csv_item.get("spec") or {}).get("customResourceDefinitions", {}).get("owned") or []
        for crd in owned:
            if (crd.get("kind") or "").strip() == "DeviceConfig" or "deviceconfig" in (crd.get("name") or "").lower():
                return csv_item
    return None


def get_amd_csv_owned_crds(oc: OcRunner) -> list[dict]:
    """Return list of owned CRD dicts (name, kind, version) from the AMD GPU Operator CSV (not KMM)."""
    csv_item = get_amd_gpu_csv_item(oc)
    if not csv_item:
        return []
    return (csv_item.get("spec") or {}).get("customResourceDefinitions", {}).get("owned") or []


def get_device_config_crd_from_amd_csv(oc: OcRunner) -> tuple[str, str] | None:
    """
    Get DeviceConfig CRD name and apiVersion from the AMD GPU Operator CSV (spec.owned).
    Returns (crd_name, api_version) e.g. ("deviceconfigs.amd.com", "amd.com/v1alpha1") or None.
    """
    for crd in get_amd_csv_owned_crds(oc):
        name = (crd.get("name") or "").strip()
        kind = (crd.get("kind") or "").strip()
        version = (crd.get("version") or "v1alpha1").strip()
        if kind == "DeviceConfig" or "deviceconfig" in name.lower():
            if "." in name:
                group = name.split(".", 1)[1]
                return (name, f"{group}/{version}")
            return (name, f"amd.com/{version}")
    return None


def get_owned_crd_names_from_amd_csv(oc: OcRunner) -> list[str]:
    """Return CRD names owned by the AMD GPU Operator CSV (for error messages)."""
    # Prefer CSV found by subscription so we show owned CRDs even when none is DeviceConfig
    r = oc.oc(
        "get", "subscription", "amd-gpu-operator", "-n", NAMESPACE_AMD_GPU,
        "-o", "jsonpath={.status.installedCSV}",
        timeout=10,
    )
    installed_csv = (r.stdout or "").strip() if r.returncode == 0 else ""
    if installed_csv:
        r2 = oc.oc("get", "csv", installed_csv, "-n", NAMESPACE_AMD_GPU, "-o", "json", timeout=10)
        if r2.returncode == 0 and r2.stdout:
            try:
                csv_item = json.loads(r2.stdout)
                owned = (csv_item.get("spec") or {}).get("customResourceDefinitions", {}).get("owned") or []
                return [crd.get("name", "").strip() for crd in owned if crd.get("name")]
            except json.JSONDecodeError:
                pass
    csv_item = get_amd_gpu_csv_item(oc)
    if not csv_item:
        return []
    owned = (csv_item.get("spec") or {}).get("customResourceDefinitions", {}).get("owned") or []
    return [crd.get("name", "").strip() for crd in owned if crd.get("name")]


def wait_for_crd(oc: OcRunner, crd_name: str, timeout: int = 120) -> None:
    """Wait for a CRD to exist and become Established."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc("get", "crd", crd_name, "--no-headers", timeout=15)
        if r.returncode != 0:
            time.sleep(5)
            continue
        r2 = oc.oc(
            "get", "crd", crd_name,
            "-o", "jsonpath={.status.conditions[?(@.type==\"Established\")].status}",
            timeout=10,
        )
        if r2.returncode == 0 and (r2.stdout or "").strip() == "True":
            return
        time.sleep(5)
    extra = ""
    amd_crds = list_amd_crds(oc)
    if amd_crds:
        extra = f" AMD-related CRDs present: {', '.join(sorted(amd_crds))}."
    # If this was the DeviceConfig wait, show what the AMD CSV owns (for different CRD name)
    owned = get_owned_crd_names_from_amd_csv(oc)
    if owned:
        extra = (extra + " " if extra else "") + f" AMD GPU Operator CSV owns: {', '.join(sorted(owned))}. If DeviceConfig uses another name, ensure the operator CSV lists it under spec.customResourceDefinitions.owned."
    if extra:
        extra = " " + extra
    raise OperatorError(f"Timeout ({timeout}s) waiting for CRD {crd_name}.{extra}")


def wait_for_device_config_crd(oc: OcRunner, timeout: int = 180) -> str:
    """Wait for AMD GPU Operator's DeviceConfig CRD to be established before creating DeviceConfig.
    The CRD is installed by the operator when its CSV is Succeeded; no AMD GPU hardware is required.
    Returns the apiVersion to use for the DeviceConfig CR (e.g. "amd.com/v1alpha1").
    """
    # Brief pause to allow the operator to register its CRDs after CSV succeeds.
    time.sleep(10)
    discovered = get_device_config_crd_from_amd_csv(oc)
    if discovered:
        crd_name, api_version = discovered
        wait_for_crd(oc, crd_name, timeout=timeout)
        return api_version
    wait_for_crd(oc, DEVICECONFIG_CRD_NAME, timeout=timeout)
    return "amd.com/v1alpha1"


def install_nfd(oc: OcRunner, timeout: int = 600) -> None:
    """Install Node Feature Discovery Operator (Red Hat) in openshift-nfd."""
    print("Installing Node Feature Discovery (NFD) Operator...")
    ensure_namespace(oc, NAMESPACE_NFD)
    create_operator_group(oc, NAMESPACE_NFD, "openshift-nfd")
    create_subscription(
        oc, NAMESPACE_NFD, "nfd", NFD_PACKAGE, NFD_CATALOG, NFD_CHANNEL
    )
    wait_for_csv(oc, NAMESPACE_NFD, timeout=timeout)
    print("  NFD Operator installed.")


def install_kmm(oc: OcRunner, timeout: int = 600) -> None:
    """Install Kernel Module Management (KMM) Operator (Red Hat) in openshift-kmm."""
    print("Installing Kernel Module Management (KMM) Operator...")
    ensure_namespace(oc, NAMESPACE_KMM)
    # KMM only supports AllNamespaces install mode; OperatorGroup must not target a single namespace
    create_operator_group(oc, NAMESPACE_KMM, "openshift-kmm", all_namespaces=True)
    create_subscription(
        oc,
        NAMESPACE_KMM,
        "kmm",
        KMM_PACKAGE,
        KMM_CATALOG,
        KMM_CHANNEL,
        starting_csv=KMM_STARTING_CSV,
    )
    wait_for_csv(oc, NAMESPACE_KMM, timeout=timeout)
    print("  KMM Operator installed.")


def install_amd_gpu_operator(
    oc: OcRunner,
    gpu_operator_version: str,
    timeout: int = 600,
) -> None:
    """Install certified AMD GPU Operator in openshift-amd-gpu.

    Args:
        oc: OcRunner instance.
        gpu_operator_version: Full version (e.g. "1.4.1") to pin via startingCSV.
        timeout: Seconds to wait for CSV to succeed.
    """
    starting_csv = f"amd-gpu-operator.v{gpu_operator_version}"
    print(f"Installing AMD GPU Operator (certified) version {gpu_operator_version} (CSV: {starting_csv})...")
    ensure_namespace(oc, NAMESPACE_AMD_GPU)
    create_operator_group(oc, NAMESPACE_AMD_GPU, "openshift-amd-gpu", all_namespaces=True)
    create_subscription(
        oc,
        NAMESPACE_AMD_GPU,
        "amd-gpu-operator",
        AMD_GPU_PACKAGE,
        AMD_GPU_CATALOG,
        AMD_GPU_CHANNEL,
        starting_csv=starting_csv,
    )
    installed_csv = wait_for_subscription_installed(
        oc, NAMESPACE_AMD_GPU, "amd-gpu-operator", timeout=timeout
    )
    wait_for_csv_by_name(oc, NAMESPACE_AMD_GPU, installed_csv, timeout=timeout)
    print("  AMD GPU Operator installed.")


def install_all_operators(
    oc: OcRunner,
    gpu_operator_version: str,
    timeout_per_operator: int = 600,
) -> None:
    """Install NFD, then KMM, then AMD GPU Operator (order per doc)."""
    install_nfd(oc, timeout=timeout_per_operator)
    install_kmm(oc, timeout=timeout_per_operator)
    install_amd_gpu_operator(oc, gpu_operator_version=gpu_operator_version, timeout=timeout_per_operator)
