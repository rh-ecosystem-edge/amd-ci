#!/usr/bin/env python
import os
import re
import requests


from workflows.gpu_operator_versions.settings import Settings
from workflows.common.utils import logger
from workflows.gpu_operator_versions.version_utils import max_version

# AMD GPU Operator GitHub Repository
GITHUB_REPO_OWNER = 'ROCm'
GITHUB_REPO_NAME = 'gpu-operator'
GITHUB_RELEASES_URL = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases'

version_not_found = '0.0.0'

def get_operator_versions(settings: Settings) -> tuple[dict, dict]:
    """
    Fetch AMD GPU Operator versions from GitHub Releases.
    
    This function fetches available versions of the AMD GPU Operator from the
    GitHub repository releases. It extracts version tags and groups them by
    minor version, keeping only the highest patch version for each.
    
    Versions with only patch 0 (e.g., 1.5.0) are considered "pending" as we wish to ignore
    them for now. They are tracked separately until a patch 1+ version is released.
    
    Returns:
        tuple: (certified_versions, pending_versions)
            - certified_versions: dict mapping minor versions to highest patch version (patch >= 1)
              Example: {'1.0': '1.0.5', '1.1': '1.1.2'}
            - pending_versions: dict mapping minor versions to their patch 0 version (not yet certified)
              Example: {'1.3': '1.3.0'}
    """
    
    return _get_versions_from_github_releases(settings)


def _get_versions_from_github_releases(settings: Settings) -> tuple[dict, dict]:
    """
    Fetch versions from GitHub Releases API.
    
    Returns:
        tuple: (certified_versions, pending_versions)
    """
    
    logger.info(f'Fetching AMD GPU Operator releases from GitHub: {GITHUB_RELEASES_URL}')
    
    headers = {'Accept': 'application/vnd.github+json'}
    
    # Add GitHub token if available for higher rate limits
    github_token = os.getenv('GITHUB_TOKEN') or os.getenv('GH_AUTH_TOKEN')
    if github_token:
        headers['Authorization'] = f'Bearer {github_token}'
        logger.info('Using GitHub token for authentication')
    else:
        logger.info('No GitHub token found, using anonymous access (rate limited)')
    
    # Fetch releases
    req = requests.get(
        GITHUB_RELEASES_URL,
        headers=headers,
        timeout=settings.request_timeout_sec,
        params={'per_page': 100}
    )
    req.raise_for_status()
    
    releases = req.json()
    logger.info(f'Received {len(releases)} releases from GitHub')
    
    # Extract tag names from releases
    # Format: gpu-operator-charts-v1.4.0, gpu-operator-charts-v1.3.1, etc.
    tags = [release['tag_name'] for release in releases if not release.get('draft', False)]
    logger.debug(f'Release tags: {tags}')
    
    return _parse_versions_from_tags(tags)


def _parse_versions_from_tags(tags: list) -> tuple[dict, dict]:
    """
    Parse version strings from GitHub release tags.
    
    Supports AMD GPU Operator tag formats:
    - gpu-operator-charts-v1.4.0
    - gpu-operator-charts-v1.3.1
    - v1.0.0, 1.0.0 (fallback for simpler formats)
    
    Versions with only patch 0 are placed in pending_versions until a patch 1+
    version is released.
    
    Args:
        tags: List of release tag names from GitHub
        
    Returns:
        tuple: (certified_versions, pending_versions)
            - certified_versions: dict mapping minor versions to highest patch versions (patch >= 1)
            - pending_versions: dict mapping minor versions to their patch 0 version
    """
    
    # Match AMD GPU Operator release format: gpu-operator-charts-v1.4.0
    # Also support simpler formats: v1.0.0, 1.0.0
    prog_full = re.compile(r'^gpu-operator-charts-v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$')
    prog_simple = re.compile(r'^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$')
    
    # Track all versions and their highest patch
    all_versions = {}
    # Track whether each minor version has a non-zero patch
    has_certified_patch = {}
    
    for tag in tags:
        # Try full format first
        match = prog_full.match(tag)
        if not match:
            # Fallback to simple format
            match = prog_simple.match(tag)
        
        if not match:
            logger.debug(f'Skipping non-version tag: {tag}')
            continue
        
        major = match.group('major')
        minor = match.group('minor')
        patch = match.group('patch')
        
        minor_key = f'{major}.{minor}'
        full_version = f'{major}.{minor}.{patch}'
        
        existing = all_versions.get(minor_key, version_not_found)
        all_versions[minor_key] = max_version(existing, full_version)
        
        # Track if this minor version has any certified (non-zero) patch
        if patch != '0':
            has_certified_patch[minor_key] = True
    
    # Separate into certified and pending versions
    certified_versions = {}
    pending_versions = {}
    
    for minor_key, full_version in all_versions.items():
        if has_certified_patch.get(minor_key, False):
            # Has a certified patch version (>= 1)
            certified_versions[minor_key] = full_version
        else:
            # Only has patch 0 - mark as pending
            pending_versions[minor_key] = full_version
            logger.info(f'Version {full_version} marked as pending (patch 0 only)')
    
    logger.info(f'Parsed {len(certified_versions)} certified and {len(pending_versions)} pending versions from {len(tags)} tags')
    logger.debug(f'Certified versions: {certified_versions}')
    logger.debug(f'Pending versions: {pending_versions}')
    return certified_versions, pending_versions

