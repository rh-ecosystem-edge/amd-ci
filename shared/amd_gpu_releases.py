"""
Fetch and parse AMD GPU Operator release versions from the ROCm/gpu-operator
GitHub repository.
"""

from __future__ import annotations

import os
import re

import requests

from shared.version_utils import max_version

GITHUB_RELEASES_URL = "https://api.github.com/repos/ROCm/gpu-operator/releases"

TAG_FULL = re.compile(
    r"^gpu-operator-charts-v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$"
)
TAG_SIMPLE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$"
)

VERSION_NOT_FOUND = "0.0.0"



def fetch_release_tags(timeout: int = 30) -> list[str]:
    """Fetch non-draft release tag names from the ROCm/gpu-operator GitHub repo"""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.get(
        GITHUB_RELEASES_URL,
        headers=headers,
        timeout=timeout,
        params={"per_page": 100},
    )
    resp.raise_for_status()
    return [r["tag_name"] for r in resp.json() if not r.get("draft", False)]


def parse_versions_from_tags(tags: list[str]) -> dict[str, str]:
    """Parse version strings from GitHub release tags.
    Supports tag formats:
    - gpu-operator-charts-v1.4.0
    - v1.0.0, 1.0.0

    For each minor version, keeps only the highest patch version.

    Args:
        tags: List of release tag names from GitHub.

    Returns:
        dict mapping minor version (e.g. "1.4") to highest full version (e.g. "1.4.1")
    """
    all_versions: dict[str, str] = {}

    for tag in tags:
        match = TAG_FULL.match(tag) or TAG_SIMPLE.match(tag)
        if not match:
            continue

        major = match.group("major")
        minor = match.group("minor")
        patch = match.group("patch")

        minor_key = f"{major}.{minor}"
        full_version = f"{major}.{minor}.{patch}"

        existing = all_versions.get(minor_key, VERSION_NOT_FOUND)
        all_versions[minor_key] = max_version(existing, full_version)

    return all_versions
