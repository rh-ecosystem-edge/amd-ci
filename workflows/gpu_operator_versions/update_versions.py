#!/usr/bin/env python
import argparse
import json
import sys
from typing import Dict, List, Optional, Set, Tuple
from workflows.common.utils import logger
from workflows.common.broken_versions import find_broken_entry, load_broken_versions

from workflows.gpu_operator_versions.settings import Settings
from workflows.gpu_operator_versions.openshift import fetch_ocp_versions
from workflows.gpu_operator_versions.version_utils import get_latest_versions
from workflows.gpu_operator_versions.amd_gpu_operator import get_operator_versions

# Constants
test_command_template = "/test {ocp_version}-stable-e2e-amd-ci-{gpu_version}"


def save_tests_commands(tests_commands: set, file_path: str):
    """Save test commands to a file, one per line."""
    with open(file_path, "w+") as f:
        for command in sorted(tests_commands):
            f.write(command + "\n")


def save_version_change_summary(
    target_ocp_version: Optional[str],
    target_gpu_version: Optional[str],
    old_versions: dict,
    new_versions: dict,
    file_path: str
):
    """
    Save a summary of version changes to a file for use in commit messages.

    Format:
    OLD_VERSION=X.Y.Z
    NEW_VERSION=X.Y.Z
    VERSION_TYPE=ocp|gpu
    MINOR_VERSION=X.Y
    """
    summary = {}

    if target_ocp_version:
        old_ver = old_versions.get("ocp", {}).get(target_ocp_version, "unknown")
        new_ver = new_versions.get("ocp", {}).get(target_ocp_version, "unknown")
        summary = {
            "OLD_VERSION": old_ver,
            "NEW_VERSION": new_ver,
            "VERSION_TYPE": "ocp",
            "MINOR_VERSION": target_ocp_version
        }
    elif target_gpu_version:
        old_ver = old_versions.get("gpu-operator", {}).get(target_gpu_version, "unknown")
        new_ver = new_versions.get("gpu-operator", {}).get(target_gpu_version, "unknown")
        summary = {
            "OLD_VERSION": old_ver,
            "NEW_VERSION": new_ver,
            "VERSION_TYPE": "gpu",
            "MINOR_VERSION": target_gpu_version
        }

    if summary:
        with open(file_path, "w") as f:
            for key, value in summary.items():
                f.write(f"{key}={value}\n")


def create_tests_matrix(diffs: dict, ocp_releases: list, gpu_releases: list) -> set:
    """
    Create a test matrix based on version changes.
    
    This generates test combinations for:
    - New OpenShift versions (tested against GPU operator releases specified in gpu_releases)
    - New GPU operator versions (tested against all OpenShift versions)
    
    Args:
        diffs: Dictionary of detected changes
        ocp_releases: List of OpenShift release versions
        gpu_releases: List of GPU operator release versions to test new OCP versions against
                      (either all versions or latest X, depending on configuration)
        
    Returns:
        set: Set of test tuples (ocp_version, gpu_version)
    """
    tests = set()

    if "ocp" in diffs:
        logger.info(f'OpenShift versions changed: {diffs["ocp"]}')
        logger.info(f'Testing new OCP versions against {len(gpu_releases)} GPU operator versions: {gpu_releases}')
        for ocp_version in diffs["ocp"]:
            if ocp_version not in ocp_releases:
                logger.warning(f'OpenShift version "{ocp_version}" is not in the list of releases: {list(ocp_releases)}. '
                               f'This should not normally happen. Check if there was an update to an old version.')
            for gpu_version in gpu_releases:
                tests.add((ocp_version, gpu_version))

    if "gpu-operator" in diffs:
        logger.info(f'AMD GPU operator versions changed: {diffs["gpu-operator"]}')
        logger.info(f'Testing new GPU operator versions against all {len(ocp_releases)} OCP versions')
        for gpu_version in diffs["gpu-operator"]:
            if gpu_version not in gpu_releases:
                logger.warning(f'AMD GPU operator version "{gpu_version}" is not in the list of releases: {list(gpu_releases)}. '
                               f'This should not normally happen. Check if there was an update to an old version.')
                continue
            for ocp_version in ocp_releases:
                tests.add((ocp_version, gpu_version))

    return tests


def filter_broken_tests(
    tests: Set[Tuple[str, str]],
    ocp_versions: Dict[str, str],
    gpu_versions: Dict[str, str],
    broken_entries: List[dict],
) -> Set[Tuple[str, str]]:
    """
    Filter out (ocp_version, gpu_version) pairs that match a broken version entry.

    Each minor version is resolved to its known full patch version (e.g. "4.21" -> "4.21.25")
    before matching, so a patch-specific broken entry automatically stops applying once that
    minor line advances to a newer, non-broken patch. Minor/major-level broken entries persist
    across patch bumps since they don't depend on the resolved patch.

    Args:
        tests: Set of (ocp_version, gpu_version) minor-version tuples to filter
        ocp_versions: Mapping of OCP minor version to its known full patch version
        gpu_versions: Mapping of GPU operator minor version to its known full patch version
        broken_entries: Broken version entries loaded from broken_versions.json

    Returns:
        set: The subset of tests that are not known to be broken
    """
    if not broken_entries:
        return tests

    filtered = set()
    for ocp_version, gpu_version in tests:
        ocp_full = ocp_versions.get(ocp_version, ocp_version)
        gpu_full = gpu_versions.get(gpu_version, gpu_version)
        entry = find_broken_entry(ocp_full, gpu_full, broken_entries)
        if entry:
            logger.info(
                f'Skipping test trigger for OCP {ocp_full} + AMD GPU Operator {gpu_full}: '
                f'marked as broken ({entry["reason"]})'
            )
            continue
        filtered.add((ocp_version, gpu_version))

    return filtered


def create_tests_commands(
    diffs: dict,
    ocp_releases: list,
    gpu_releases: list,
    ocp_versions: Optional[Dict[str, str]] = None,
    gpu_versions: Optional[Dict[str, str]] = None,
    broken_entries: Optional[List[dict]] = None,
) -> set:
    """
    Create test commands from the test matrix, excluding any combos marked as broken.
    
    Args:
        diffs: Dictionary of detected changes
        ocp_releases: List of OpenShift release versions
        gpu_releases: List of GPU operator release versions to test new OCP versions against
        ocp_versions: Mapping of OCP minor version to its known full patch version
        gpu_versions: Mapping of GPU operator minor version to its known full patch version
        broken_entries: Broken version entries loaded from broken_versions.json
        
    Returns:
        set: Set of test command strings
    """
    tests_commands = set()
    tests = create_tests_matrix(diffs, ocp_releases, gpu_releases)
    tests = filter_broken_tests(tests, ocp_versions or {}, gpu_versions or {}, broken_entries or [])
    for t in tests:
        gpu_version_suffix = version2suffix(t[1])
        tests_commands.add(test_command_template.format(ocp_version=t[0], gpu_version=gpu_version_suffix))
    return tests_commands


def calculate_diffs(old_versions: dict, new_versions: dict) -> dict:
    """
    Recursively calculate differences between old and new version dictionaries.
    
    Args:
        old_versions: Previously stored versions
        new_versions: Newly fetched versions
        
    Returns:
        dict: Dictionary containing only the changed/new items
    """
    diffs = {}
    for key, value in new_versions.items():
        if isinstance(value, dict):
            logger.info(f'Comparing versions under "{key}"')
            sub_diff = calculate_diffs(old_versions.get(key, {}), value)
            if sub_diff:
                diffs[key] = sub_diff
        else:
            if key not in old_versions or old_versions[key] != value:
                logger.info(f'Key "{key}" has changed: {old_versions.get(key)} > {value}')
                diffs[key] = value

    return diffs


def version2suffix(v: str):
    """Convert version to test suffix format."""
    return f'{v.replace(".", "-")}-x'


def main():
    """
    Main function to detect version changes and generate test commands.

    Modes:
    1. Default: Detect all changes, update versions.json, generate all test commands
    2. --detect-only: Detect changes and output as JSON, don't modify files
    3. --target-ocp-version X.Y: Update only specific OCP version, generate tests for it
    4. --target-gpu-version X.Y: Update only specific GPU version, generate tests for it
    """
    parser = argparse.ArgumentParser(description='AMD GPU Operator version detection and testing')
    parser.add_argument('--detect-only', action='store_true',
                       help='Detect changes and output as JSON without modifying files')
    parser.add_argument('--target-ocp-version', type=str,
                       help='Update only this specific OCP minor version (e.g., 4.22)')
    parser.add_argument('--target-gpu-version', type=str,
                       help='Update only this specific GPU operator version (e.g., 1.5)')
    args = parser.parse_args()

    logger.info('Starting AMD GPU Operator CI version detection')

    settings = Settings()

    # Fetch current versions
    logger.info('Fetching AMD GPU operator versions...')
    gpu_versions = get_operator_versions(settings)

    logger.info('Fetching OpenShift versions...')
    ocp_versions = fetch_ocp_versions(settings)

    new_versions = {
        "gpu-operator": gpu_versions,
        "ocp": ocp_versions
    }

    logger.info(f'Fetched versions: OCP={len(ocp_versions)}, GPU={len(gpu_versions)}')

    # Load old versions
    with open(settings.version_file_path, "r") as json_f:
        old_versions = json.load(json_f)

    # Calculate differences
    diffs = calculate_diffs(old_versions, new_versions)

    if not diffs:
        logger.info('No version changes detected')
    else:
        logger.info(f'Detected changes in: {list(diffs.keys())}')

    # DETECT-ONLY MODE: Output changes as JSON and exit
    if args.detect_only:
        output = {
            "ocp_versions": list(diffs.get("ocp", {}).keys()),
            "gpu_versions": list(diffs.get("gpu-operator", {}).keys()),
            "ocp_details": diffs.get("ocp", {}),
            "gpu_details": diffs.get("gpu-operator", {})
        }
        print(json.dumps(output))
        logger.info('Detection complete (detect-only mode)')
        return

    # TARGET MODE: Update only specific version(s)
    versions_to_update = {}
    target_diffs = {}

    if args.target_ocp_version:
        logger.info(f'Target mode: OCP version {args.target_ocp_version}')
        if args.target_ocp_version in new_versions["ocp"]:
            versions_to_update["ocp"] = {
                args.target_ocp_version: new_versions["ocp"][args.target_ocp_version]
            }
            if args.target_ocp_version in diffs.get("ocp", {}):
                target_diffs["ocp"] = {args.target_ocp_version: diffs["ocp"][args.target_ocp_version]}
        else:
            logger.error(f'Target OCP version {args.target_ocp_version} not found in fetched versions')
            sys.exit(1)

    if args.target_gpu_version:
        logger.info(f'Target mode: GPU operator version {args.target_gpu_version}')
        if args.target_gpu_version in new_versions["gpu-operator"]:
            versions_to_update["gpu-operator"] = {
                args.target_gpu_version: new_versions["gpu-operator"][args.target_gpu_version]
            }
            if args.target_gpu_version in diffs.get("gpu-operator", {}):
                target_diffs["gpu-operator"] = {args.target_gpu_version: diffs["gpu-operator"][args.target_gpu_version]}
        else:
            logger.error(f'Target GPU version {args.target_gpu_version} not found in fetched versions')
            sys.exit(1)

    # Determine what to update and what diffs to use for test generation
    if versions_to_update:
        # Target mode: partial update
        update_versions = versions_to_update
        test_diffs = target_diffs
    else:
        # Default mode: full update
        update_versions = new_versions
        test_diffs = diffs

    # Update version file with selected versions
    with open(settings.version_file_path, "r+") as json_f:
        current = json.load(json_f)
        # Merge updates into current versions
        for category, versions in update_versions.items():
            if category not in current:
                current[category] = {}
            current[category].update(versions)
        json_f.seek(0)
        json.dump(current, json_f, indent=4)
        json_f.truncate()

    logger.info('Version file updated')

    # Generate test commands
    ocp_releases = list(ocp_versions.keys())
    all_gpu_releases = list(gpu_versions.keys())

    # Determine GPU releases to test new OCP versions against
    if settings.gpu_versions_to_test_count is not None:
        gpu_releases_for_ocp = get_latest_versions(all_gpu_releases, settings.gpu_versions_to_test_count)
        logger.info(f'GPU_VERSIONS_TO_TEST_COUNT={settings.gpu_versions_to_test_count} - '
                    f'new OCP versions will be tested against {len(gpu_releases_for_ocp)} latest GPU releases: {gpu_releases_for_ocp}')
    else:
        gpu_releases_for_ocp = all_gpu_releases
        logger.info(f'GPU_VERSIONS_TO_TEST_COUNT not set - '
                    f'new OCP versions will be tested against ALL {len(gpu_releases_for_ocp)} GPU releases')

    logger.info(f'Generating tests for {len(ocp_releases)} OCP releases')
    logger.info(f'New GPU versions will be tested against all {len(ocp_releases)} OCP releases')

    broken_entries = load_broken_versions(settings.broken_versions_file_path)
    tests_commands = create_tests_commands(
        test_diffs, ocp_releases, gpu_releases_for_ocp, ocp_versions, gpu_versions, broken_entries
    )

    logger.info(f'Generated {len(tests_commands)} test commands')
    save_tests_commands(tests_commands, settings.tests_to_trigger_file_path)

    # Save version change summary for commit messages (target mode only)
    if settings.version_change_summary_file_path and (args.target_ocp_version or args.target_gpu_version):
        save_version_change_summary(
            args.target_ocp_version,
            args.target_gpu_version,
            old_versions,
            new_versions,
            settings.version_change_summary_file_path
        )
        logger.info(f'Version change summary saved to {settings.version_change_summary_file_path}')

    logger.info('AMD GPU Operator CI version detection complete')


if __name__ == '__main__':
    main()

