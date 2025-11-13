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
GITHUB_TAGS_URL = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/tags'

# GitHub Container Registry for getting latest main SHA
AMD_GPU_OPERATOR_GHCR_AUTH_URL = 'https://ghcr.io/token?scope=repository:rocm/gpu-operator:pull'
AMD_GPU_OPERATOR_GHCR_LATEST_URL = 'https://ghcr.io/v2/rocm/gpu-operator/gpu-operator-bundle/manifests/main-latest'

version_not_found = '0.0.0'

def get_operator_versions(settings: Settings) -> dict:
    """
    Fetch AMD GPU Operator versions from GitHub Releases.
    
    This function fetches available versions of the AMD GPU Operator from the
    GitHub repository releases. It extracts version tags and groups them by
    minor version, keeping only the highest patch version for each.
    
    Returns:
        dict: Dictionary mapping minor versions to their highest patch version
              Example: {'1.0': '1.0.5', '1.1': '1.1.2'}
    """
    
    return _get_versions_from_github_releases(settings)


def _get_versions_from_github_releases(settings: Settings) -> dict:
    """
    Fetch versions from GitHub Releases API.
    
    Returns:
        dict: Dictionary mapping minor versions to their highest patch version
    """
    
    logger.info(f'Fetching AMD GPU Operator releases from GitHub: {GITHUB_RELEASES_URL}')
    
    try:
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
        
    except requests.RequestException as e:
        logger.error(f'Failed to fetch releases from GitHub: {e}')
        logger.warning('Returning empty versions dict')
        return {}
    
    return _parse_versions_from_tags(tags)


def _parse_versions_from_tags(tags: list) -> dict:
    """
    Parse version strings from GitHub release tags.
    
    Supports AMD GPU Operator tag formats:
    - gpu-operator-charts-v1.4.0
    - gpu-operator-charts-v1.3.1
    - v1.0.0, 1.0.0 (fallback for simpler formats)
    
    Args:
        tags: List of release tag names from GitHub
        
    Returns:
        dict: Dictionary mapping minor versions to highest patch versions
              Example: {'1.4': '1.4.0', '1.3': '1.3.1'}
    """
    
    # Match AMD GPU Operator release format: gpu-operator-charts-v1.4.0
    # Also support simpler formats: v1.0.0, 1.0.0
    prog_full = re.compile(r'^gpu-operator-charts-v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$')
    prog_simple = re.compile(r'^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$')
    
    versions = {}
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
        
        existing = versions.get(minor_key, version_not_found)
        versions[minor_key] = max_version(existing, full_version)
    
    logger.info(f'Parsed {len(versions)} unique minor versions from {len(tags)} tags')
    logger.debug(f'Parsed versions: {versions}')
    return versions


def get_sha(settings: Settings) -> str:
    """
    Get the SHA of the latest commit on the main branch of AMD GPU Operator.
    
    This is used to track changes to the development/main branch of the operator.
    
    Returns:
        str: SHA of the latest commit on main branch
    """
    
    logger.info('Getting latest commit SHA from main branch')
    
    try:
        headers = {'Accept': 'application/vnd.github+json'}
        
        # Add GitHub token if available for higher rate limits
        github_token = os.getenv('GITHUB_TOKEN') or os.getenv('GH_AUTH_TOKEN')
        if github_token:
            headers['Authorization'] = f'Bearer {github_token}'
            logger.info('Using GitHub token for authentication')
        else:
            logger.info('No GitHub token found, using anonymous access')
        
        # Get the latest commit from main branch
        commits_url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/commits/main'
        req = requests.get(
            commits_url,
            headers=headers,
            timeout=settings.request_timeout_sec
        )
        req.raise_for_status()
        
        commit_data = req.json()
        sha = commit_data['sha']
        logger.info(f'Latest commit SHA: {sha}')
        return sha
        
    except requests.RequestException as e:
        logger.warning(f'Failed to get commit SHA: {e}')
        logger.warning('Returning placeholder SHA')
        return '0000000000000000000000000000000000000000'

