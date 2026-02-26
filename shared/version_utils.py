"""Version comparison utilities shared across the project."""

from semver import Version


def max_version(a: str, b: str) -> str:
    """Parse and compare two semver versions. Return the higher of them."""
    return str(max(map(Version.parse, (a, b))))
