#!/usr/bin/env python

from workflows.gpu_operator_versions.settings import Settings
from workflows.common.utils import logger
from shared.amd_gpu_releases import (
    GITHUB_RELEASES_URL,
    fetch_release_tags,
    parse_versions_from_tags,
)


def get_operator_versions(settings: Settings) -> dict:
    """
    Fetch AMD GPU Operator versions from GitHub Releases.
    
    This function fetches available versions of the AMD GPU Operator from the
    GitHub repository releases. It extracts version tags and groups them by
    minor version, keeping only the highest patch version for each.
    
    Returns:
        dict mapping minor versions to highest patch version.
        Example: {'1.0': '1.0.5', '1.1': '1.1.2', '1.3': '1.3.0'}
    """
    logger.info(f'Fetching AMD GPU Operator releases from GitHub: {GITHUB_RELEASES_URL}')

    tags = fetch_release_tags(timeout=settings.request_timeout_sec)
    logger.info(f'Received {len(tags)} release tags from GitHub')
    logger.debug(f'Release tags: {tags}')

    versions = parse_versions_from_tags(tags)

    logger.info(f'Parsed {len(versions)} versions from {len(tags)} tags')
    logger.debug(f'Versions: {versions}')

    return versions
