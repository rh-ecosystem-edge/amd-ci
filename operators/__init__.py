"""
AMD GPU Operator and dependencies installation for OpenShift (OLM).

After cluster install, this package can install and configure:
- Prerequisites (required operators, internal registry)
- NFD, KMM, and AMD GPU Operator via OLM
- NFD rules, amdgpu blacklist MachineConfig, DeviceConfig, cluster monitoring

Reference: https://instinct.docs.amd.com/projects/gpu-operator/en/release-v1.4.1/installation/openshift-olm.html
"""

from operators.cleanup import cleanup_operators
from operators.main import install_operators

__all__ = ["install_operators", "cleanup_operators"]
