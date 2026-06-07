"""
Resolve AMD GPU Operator version to the latest patch version
using the shared GitHub Releases logic.
"""

from __future__ import annotations

import re

from shared.amd_gpu_releases import fetch_release_tags, parse_versions_from_tags


def resolve_latest_patch(version: str, timeout: int = 30) -> str:
    """Resolve a major.minor version (e.g. "1.4") to the latest patch release (e.g. "1.4.1").

    Args:
        version: Version in "major.minor" format (e.g. "1.4").
        timeout: HTTP request timeout in seconds.

    Returns:
        Full version string (e.g. "1.4.1").

    Raises:
        ValueError: If no release is found for the given version or the format is invalid.
    """
    # If already a full patch version (major.minor.patch), validate format and skip GitHub lookup.
    if version.count(".") >= 2:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError(
                f'Invalid AMD GPU Operator version "{version}". Expected "X.Y.Z".'
            )
        print(f"Resolved AMD GPU Operator {version} -> {version} (already pinned)")
        return version

    tags = fetch_release_tags(timeout=timeout)
    versions = parse_versions_from_tags(tags)

    if version not in versions:
        raise ValueError(
            f'No GitHub release found for AMD GPU Operator version "{version}". '
            f"Available versions: {sorted(versions.keys())}. "
            f"Check https://github.com/ROCm/gpu-operator/releases"
        )

    resolved = versions[version]
    print(f"Resolved AMD GPU Operator {version} -> {resolved}")
    return resolved
