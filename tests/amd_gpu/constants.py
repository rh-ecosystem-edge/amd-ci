"""Constants for AMD GPU operator verification tests."""

import os

# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------
NAMESPACE_AMD_GPU = os.environ.get("AMD_GPU_NAMESPACE", "openshift-amd-gpu")
NAMESPACE_IMAGE_REGISTRY = "openshift-image-registry"

# ---------------------------------------------------------------------------
# DeviceConfig custom resource
# ---------------------------------------------------------------------------
DEVICECONFIG_NAME = os.environ.get("AMD_DEVICECONFIG_NAME", "amd-gpu-device-config")
DEVICECONFIG_GROUP = os.environ.get("AMD_DEVICECONFIG_GROUP", "amd.com")
DEVICECONFIG_VERSION = os.environ.get("AMD_DEVICECONFIG_VERSION", "v1alpha1")
DEVICECONFIG_PLURAL = "deviceconfigs"

# ---------------------------------------------------------------------------
# GPU extended resource
# ---------------------------------------------------------------------------
GPU_RESOURCE_NAME = "amd.com/gpu"

# ---------------------------------------------------------------------------
# NFD label applied to nodes with AMD GPUs
# ---------------------------------------------------------------------------
NFD_LABEL_KEY = "feature.node.kubernetes.io/amd-gpu"
NFD_LABEL_VALUE = "true"

# ---------------------------------------------------------------------------
# Pod naming prefixes (derived from DeviceConfig name)
# ---------------------------------------------------------------------------
DEVICE_PLUGIN_PREFIX = f"{DEVICECONFIG_NAME}-device-plugin-"
NODE_LABELLER_PREFIX = f"{DEVICECONFIG_NAME}-node-labeller-"

# ---------------------------------------------------------------------------
# ROCm test image
# ---------------------------------------------------------------------------
ROCM_TEST_IMAGE = os.environ.get(
    "AMD_ROCM_TEST_IMAGE", "rocm/rocm-terminal:latest"
)

# ---------------------------------------------------------------------------
# Labels applied by the Node Labeller
# ---------------------------------------------------------------------------
NODE_LABELLER_LABELS = [
    "amd.com/gpu.cu-count",
    "amd.com/gpu.device-id",
    "amd.com/gpu.driver-version",
    "amd.com/gpu.family",
    "amd.com/gpu.simd-count",
    "amd.com/gpu.vram",
    "beta.amd.com/gpu.cu-count",
    "beta.amd.com/gpu.device-id",
    "beta.amd.com/gpu.family",
    "beta.amd.com/gpu.simd-count",
    "beta.amd.com/gpu.vram",
]

# ---------------------------------------------------------------------------
# Supported AMD GPU device IDs
# ---------------------------------------------------------------------------
DEVICE_IDS: dict[str, str] = {
    "74a5": "MI325X",
    "74a2": "MI308X",
    "74a8": "MI308X-HF",
    "74a0": "MI300A",
    "74a1": "MI300X",
    "74a9": "MI300X-HF",
    "74bd": "MI300X-HF",
    "740f": "MI210",
    "7408": "MI250X",
    "740c": "MI250/MI250X",
    "738c": "MI100",
    "738e": "MI100",
}

# ---------------------------------------------------------------------------
# Timeouts and polling intervals (seconds)
# ---------------------------------------------------------------------------
POD_COMPLETION_TIMEOUT = 300
POD_DELETION_TIMEOUT = 60
POD_COMPLETION_POLL_INTERVAL = 5
POD_DELETION_POLL_INTERVAL = 2
