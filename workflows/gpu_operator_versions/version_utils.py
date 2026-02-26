"""Version comparison utilities for AMD GPU operator workflows."""

from shared.version_utils import max_version

def get_latest_versions(versions: list, count: int) -> list:
    if count <= 0:
        raise ValueError("count must be positive")
    sorted_versions = get_sorted_versions(versions)
    return sorted_versions[-count:] if len(sorted_versions) > count else sorted_versions


def get_earliest_versions(versions: list, count: int) -> list:
    if count <= 0:
        raise ValueError("count must be positive")
    sorted_versions = get_sorted_versions(versions)
    return sorted_versions[:count] if len(sorted_versions) > count else sorted_versions


def get_sorted_versions(versions: list) -> list:
    return sorted(versions, key=lambda v: tuple(map(int, v.split('.'))))

