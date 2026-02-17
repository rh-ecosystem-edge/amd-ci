"""
Constants for AMD GPU Operator and dependency operators (OLM).
"""

# Namespaces
NAMESPACE_NFD = "openshift-nfd"
NAMESPACE_KMM = "openshift-kmm"
NAMESPACE_AMD_GPU = "openshift-amd-gpu"

# OLM subscription: (package_name, catalog_source, channel)
# Red Hat NFD Operator
NFD_PACKAGE = "nfd"
NFD_CATALOG = "redhat-operators"
NFD_CHANNEL = "stable"

# Red Hat KMM Operator (Kernel Module Management)
# Package per Red Hat OCP doc. Channel can vary by catalog: if release-1.0 fails with
# "no operators found in channel", check: oc get packagemanifest kernel-module-management -n openshift-marketplace -o jsonpath='{.status.channels[*].name}'
KMM_PACKAGE = "kernel-module-management"
KMM_CATALOG = "redhat-operators"
# Prefer stable (common in newer catalogs); fallback release-1.0 is in older docs
KMM_CHANNEL = "stable"
# Optional: pin CSV. Leave None to let OLM pick from channel; set if channel has multiple and one is known good
KMM_STARTING_CSV = None

# Certified AMD GPU Operator
# Verify with: oc get packagemanifest amd-gpu-operator -n openshift-marketplace -o jsonpath='{.status.channels[*].name}'
AMD_GPU_PACKAGE = "amd-gpu-operator"
AMD_GPU_CATALOG = "certified-operators"
# Channel: use "alpha" if "stable" has no bundles (ResolutionFailed)
AMD_GPU_CHANNEL = "alpha"

# Internal registry
REGISTRY_NAMESPACE = "openshift-image-registry"
REGISTRY_PATCH_STORAGE = '{"spec":{"storage":{"emptyDir":{}}}}'
REGISTRY_PATCH_MANAGED = '{"spec":{"managementState":"Managed"}}'

# MachineConfig for amdgpu blacklist (base64: "blacklist amdgpu\n")
AMDGPU_BLACKLIST_CONTENTS_B64 = "YmxhY2tsaXN0IGFtZGdwdQo="

# DeviceConfig (CRD installed by AMD GPU Operator)
DEVICECONFIG_CRD_NAME = "deviceconfigs.amd.com"
DEVICECONFIG_NAME = "amd-gpu-device-config"
DEFAULT_DRIVER_VERSION = "30.20.1"
AMD_GPU_LABEL = "feature.node.kubernetes.io/amd-gpu"
DEFAULT_DRIVER_IMAGE = "image-registry.openshift-image-registry.svc:5000/$MOD_NAMESPACE/amdgpu_kmod"

# NodeFeatureDiscovery instance name (starts NFD pods)
NFD_INSTANCE_NAME = "amd-gpu-nfd-instance"
# NodeFeatureRule name (separate CR for AMD GPU detection)
NFD_FEATURE_RULE_NAME = "amd-gpu-feature-rule"

# MachineConfig
MACHINECONFIG_AMDGPU_BLACKLIST = "amdgpu-module-blacklist"
