#!/usr/bin/env python
import json
from workflows.common.utils import logger

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


def create_tests_commands(diffs: dict, ocp_releases: list, gpu_releases: list) -> set:
    """
    Create test commands from the test matrix.
    
    Args:
        diffs: Dictionary of detected changes
        ocp_releases: List of OpenShift release versions
        gpu_releases: List of GPU operator release versions to test new OCP versions against
        
    Returns:
        set: Set of test command strings
    """
    tests_commands = set()
    tests = create_tests_matrix(diffs, ocp_releases, gpu_releases)
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
    
    Process:
    1. Fetch current AMD GPU operator versions (certified and pending)
    2. Fetch current OpenShift versions
    3. Load previously stored versions
    4. Calculate differences
    5. Generate test matrix
    6. Save test commands to file
    7. Update version file
    """
    logger.info('Starting AMD GPU Operator CI version detection')
    
    settings = Settings()
    
    # Fetch current versions
    logger.info('Fetching AMD GPU operator versions...')
    gpu_versions, gpu_pending_versions = get_operator_versions(settings)
    
    logger.info('Fetching OpenShift versions...')
    ocp_versions = fetch_ocp_versions(settings)

    new_versions = {
        "gpu-operator": gpu_versions,
        "gpu-operator-pending": gpu_pending_versions,
        "ocp": ocp_versions
    }
    
    logger.info(f'Fetched versions: OCP={len(ocp_versions)}, GPU={len(gpu_versions)} certified, {len(gpu_pending_versions)} pending')

    # Load old versions and update file
    with open(settings.version_file_path, "r+") as json_f:
        old_versions = json.load(json_f)
        json_f.seek(0)
        json.dump(new_versions, json_f, indent=4)
        json_f.truncate()
    
    logger.info('Version file updated')

    # Calculate differences
    diffs = calculate_diffs(old_versions, new_versions)
    
    if not diffs:
        logger.info('No version changes detected')
    else:
        logger.info(f'Detected changes in: {list(diffs.keys())}')
    
    # Generate test commands
    ocp_releases = list(ocp_versions.keys())
    all_gpu_releases = list(gpu_versions.keys())
    
    # Determine GPU releases to test new OCP versions against
    # Default: Test against ALL GPU operator versions
    # If GPU_VERSIONS_TO_TEST_COUNT env var is set: Test against only the latest X versions
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
    tests_commands = create_tests_commands(diffs, ocp_releases, gpu_releases_for_ocp)
    
    logger.info(f'Generated {len(tests_commands)} test commands')
    save_tests_commands(tests_commands, settings.tests_to_trigger_file_path)
    
    logger.info('AMD GPU Operator CI version detection complete')


if __name__ == '__main__':
    main()

