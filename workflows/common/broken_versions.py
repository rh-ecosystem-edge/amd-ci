"""Utilities for loading and matching the manually-maintained broken_versions.json file.

The file lets the team mark OCP / AMD GPU Operator version combinations as broken so that:
  - the version-detection workflow stops suggesting `/test` commands for them, and
  - the CI dashboard stops showing them (and instead surfaces a note explaining why).

Each entry is a dict with an optional "ocp_version", an optional "gpu_operator_version"
(at least one of the two is required), and a required "reason". A version field can be
given at any granularity - major ("4"), minor ("4.21"), or patch ("4.21.6") - and matches
via a dot-segment prefix match against the concrete version being checked. Omitting a
field means "any version" for that product (a wildcard on the other axis).
"""

import json
import os
from typing import Any, Dict, List, Optional

from workflows.common.utils import logger

OCP_VERSION_KEY = "ocp_version"
GPU_OPERATOR_VERSION_KEY = "gpu_operator_version"
REASON_KEY = "reason"


def _version_matches(spec: str, version: str) -> bool:
    """Return True if spec is a dot-separated segment prefix of version.

    e.g. "4" matches "4.21.6"; "4.21" matches "4.21.6"; "4.21.6" matches "4.21.6" only.
    "4.2" does NOT match "4.20" (segments are compared whole, not as raw string prefixes).
    """
    spec_parts = spec.split(".")
    version_parts = version.split(".")
    return version_parts[:len(spec_parts)] == spec_parts


def _validate_optional_string_field(entry: Dict[str, Any], key: str, index: int, path: str) -> None:
    value = entry.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(
            f'Invalid entry #{index} in "{path}": "{key}" must be a non-empty string, got {value!r}'
        )


def _validate_entry(entry: Any, index: int, path: str) -> None:
    if not isinstance(entry, dict):
        raise ValueError(
            f'Invalid entry #{index} in "{path}": expected a JSON object, got {entry!r}'
        )

    _validate_optional_string_field(entry, OCP_VERSION_KEY, index, path)
    _validate_optional_string_field(entry, GPU_OPERATOR_VERSION_KEY, index, path)

    ocp_version = entry.get(OCP_VERSION_KEY)
    gpu_version = entry.get(GPU_OPERATOR_VERSION_KEY)
    reason = entry.get(REASON_KEY)

    if not ocp_version and not gpu_version:
        raise ValueError(
            f'Invalid entry #{index} in "{path}": must specify at least one of '
            f'"{OCP_VERSION_KEY}" or "{GPU_OPERATOR_VERSION_KEY}" '
            f'(an entry with neither would match every version)'
        )
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(
            f'Invalid entry #{index} in "{path}": "{REASON_KEY}" must be a non-empty string, got {reason!r}'
        )


def load_broken_versions(path: Optional[str]) -> List[Dict[str, Any]]:
    """Load and validate broken version entries from a JSON file.

    Returns an empty list (with a warning logged) if path is not set or the file
    does not exist. Raises ValueError if the file exists but contains malformed
    entries, since this is a hand-edited file and mistakes should fail loudly.
    """
    if not path:
        logger.warning('No broken versions file path configured; treating as no broken versions')
        return []

    if not os.path.exists(path):
        logger.warning(f'Broken versions file "{path}" does not exist; treating as no broken versions')
        return []

    with open(path, "r") as f:
        entries = json.load(f)

    if not isinstance(entries, list):
        raise ValueError(f'Broken versions file "{path}" must contain a JSON list')

    for index, entry in enumerate(entries):
        _validate_entry(entry, index, path)

    logger.info(f'Loaded {len(entries)} broken version entries from "{path}"')
    return entries


def find_broken_entry(
    ocp_version: Optional[str],
    gpu_version: Optional[str],
    entries: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the first entry matching the given (ocp_version, gpu_version) pair, or None.

    An entry matches when every version field it specifies is a prefix-match of the
    corresponding candidate version. A field the entry omits is treated as a wildcard.
    If the entry specifies a field but the candidate doesn't have a value for it, there
    is no match (we only match against concrete, known versions).
    """
    for entry in entries:
        entry_ocp = entry.get(OCP_VERSION_KEY)
        entry_gpu = entry.get(GPU_OPERATOR_VERSION_KEY)

        if entry_ocp:
            if not ocp_version or not _version_matches(entry_ocp, ocp_version):
                continue
        if entry_gpu:
            if not gpu_version or not _version_matches(entry_gpu, gpu_version):
                continue

        return entry

    return None


def is_broken(
    ocp_version: Optional[str],
    gpu_version: Optional[str],
    entries: List[Dict[str, Any]],
) -> bool:
    """Return True if the given (ocp_version, gpu_version) pair matches a broken entry."""
    return find_broken_entry(ocp_version, gpu_version, entries) is not None


def format_broken_note(entry: Dict[str, Any]) -> str:
    """Format a broken version entry as a human-readable dashboard note."""
    ocp_version = entry.get(OCP_VERSION_KEY)
    gpu_version = entry.get(GPU_OPERATOR_VERSION_KEY)
    reason = entry[REASON_KEY]

    if ocp_version and gpu_version:
        return f"GPU Operator {gpu_version} is broken on OpenShift {ocp_version}: {reason}"
    if gpu_version:
        return f"GPU Operator {gpu_version} is broken on all OpenShift versions: {reason}"
    return f"All GPU Operator versions are broken on OpenShift {ocp_version}: {reason}"


def notes_for_ocp_key(ocp_minor_key: str, entries: List[Dict[str, Any]]) -> List[str]:
    """Return dashboard notes for entries relevant to a given OCP minor-version bucket.

    An entry is relevant to the bucket when its ocp_version (if any) matches the bucket
    via a bidirectional dot-segment prefix match (so a bucket "4.21" matches entries
    specifying "4", "4.21", or "4.21.6"), or when the entry omits ocp_version entirely
    (a GPU-wide ban applies to every OCP bucket).
    """
    notes = []
    for entry in entries:
        entry_ocp = entry.get(OCP_VERSION_KEY)
        if entry_ocp and not (
            _version_matches(entry_ocp, ocp_minor_key) or _version_matches(ocp_minor_key, entry_ocp)
        ):
            continue
        notes.append(format_broken_note(entry))
    return notes
