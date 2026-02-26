#!/usr/bin/env python

from workflows.gpu_operator_versions.settings import Settings
from workflows.common.utils import logger
from shared.amd_gpu_releases import (
    GITHUB_RELEASES_URL,
    fetch_release_tags,
    parse_versions_from_tags,
)


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
    logger.info(f'Fetching AMD GPU Operator releases from GitHub: {GITHUB_RELEASES_URL}')

    tags = fetch_release_tags(timeout=settings.request_timeout_sec)
    logger.info(f'Received {len(tags)} release tags from GitHub')
    logger.debug(f'Release tags: {tags}')

    certified, pending = parse_versions_from_tags(tags)

    for minor_key, full_version in pending.items():
        logger.info(f'Version {full_version} marked as pending (patch 0 only)')

    logger.info(f'Parsed {len(certified)} certified and {len(pending)} pending versions from {len(tags)} tags')
    logger.debug(f'Certified versions: {certified}')
    logger.debug(f'Pending versions: {pending}')

    return certified, pending
