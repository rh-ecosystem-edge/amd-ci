"""
Resolve AMD GPU Operator version to the latest patch version
using the shared GitHub Releases logic.
"""

from __future__ import annotations

from shared.amd_gpu_releases import fetch_release_tags, parse_versions_from_tags


def resolve_latest_patch(version: str, timeout: int = 30) -> str:
    """Resolve a major.minor version (e.g. "1.4") to the latest patch release (e.g. "1.4.1").

    Args:
        version: Version in "major.minor" format (e.g. "1.4").
        timeout: HTTP request timeout in seconds.

    Returns:
        Full version string (e.g. "1.4.1").

    Raises:
        ValueError: If no certified release is found for the given version.
    """
    tags = fetch_release_tags(timeout=timeout)
    certified, pending = parse_versions_from_tags(tags)

    if version not in certified:
        raise ValueError(
            f'No certified GitHub release found for AMD GPU Operator version "{version}". '
            f"Available certified versions: {sorted(certified.keys())}. "
            f"Check https://github.com/ROCm/gpu-operator/releases"
        )

    resolved = certified[version]
    print(f"Resolved AMD GPU Operator {version} -> {resolved}")
    return resolved
