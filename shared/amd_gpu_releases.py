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


def parse_versions_from_tags(tags: list[str]) -> tuple[dict, dict]:
    """Parse version strings from GitHub release tags.
    Supports tag formats:
    - gpu-operator-charts-v1.4.0
    - v1.0.0, 1.0.0

    Versions with only patch 0 are placed in pending_versions until a
    patch >= 1 version is released.

    Args:
        tags: List of release tag names from GitHub.

    Returns:
        (certified_versions, pending_versions)
        - certified_versions: minor -> highest full version (patch >= 1)
        - pending_versions:   minor -> patch-0 version
    """
    all_versions: dict[str, str] = {}
    has_certified_patch: dict[str, bool] = {}

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

        if patch != "0":
            has_certified_patch[minor_key] = True

    certified: dict[str, str] = {}
    pending: dict[str, str] = {}

    for minor_key, full_version in all_versions.items():
        if has_certified_patch.get(minor_key, False):
            certified[minor_key] = full_version
        else:
            pending[minor_key] = full_version

    return certified, pending
