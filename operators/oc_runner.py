"""
Re-export OcRunner classes from shared.oc_runner.

All OcRunner implementations live in shared/oc_runner.py.
This module re-exports them for backward compatibility with existing
operators/ imports (e.g. ``from operators.oc_runner import OcRunner``).
"""

from shared.oc_runner import (  # noqa: F401
    OcRunner,
    LocalOcRunner,
    RemoteOcRunner,
    REMOTE_KUBECONFIG,
)
