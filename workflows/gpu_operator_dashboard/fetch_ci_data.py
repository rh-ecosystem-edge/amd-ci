#!/usr/bin/env python
import argparse
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Set

import requests

from workflows.common.utils import logger


OCP_FULL_VERSION = "ocp_full_version"
GPU_OPERATOR_VERSION = "gpu_operator_version"

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILURE = "FAILURE"
STATUS_ABORTED = "ABORTED"


# =============================================================================
# Constants
# =============================================================================

GCS_API_BASE_URL = "https://storage.googleapis.com/storage/v1/b/test-platform-results/o"

# Job name pattern for amd-ci e2e tests:
#   pull-ci-rh-ecosystem-edge-amd-ci-main-<ocp_version>-stable-e2e-amd-ci[-<gpu_version>]
# gpu_version is optional: e.g. "1-3-x", "1-4-x", or absent (meaning master/latest)
TEST_RESULT_PATH_REGEX = re.compile(
    r"pr-logs/pull/(?P<repo>[^/]+)/(?P<pr_number>\d+)/"
    r"(?P<job_name>(?:rehearse-\d+-)?pull-ci-rh-ecosystem-edge-amd-ci-main-"
    r"(?P<ocp_version>\d+\.\d+)-stable-e2e-amd-ci(?:-(?P<gpu_version>\d+-\d+-x))?)/"
    r"(?P<build_id>[^/]+)"
)

GCS_MAX_RESULTS_PER_REQUEST = 1000


# =============================================================================
# Data Fetching & JSON Update Functions
# =============================================================================

def http_get_json(url: str, params: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None) -> Dict[str, Any]:
    """Send an HTTP GET request and return the JSON response."""
    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_gcs_file_content(file_path: str) -> str:
    """Fetch the raw text content from a file in GCS."""
    logger.info(f"Fetching file content for {file_path}")
    response = requests.get(
        url=f"{GCS_API_BASE_URL}/{urllib.parse.quote_plus(file_path)}",
        params={"alt": "media"},
        timeout=30,
    )
    response.raise_for_status()
    return response.content.decode("UTF-8")


def build_prow_job_url(finished_json_path: str) -> str:
    directory_path = finished_json_path[:-len('/finished.json')]
    return f"https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/{directory_path}"


SEMVER_REGEX = re.compile(r"^\d+\.\d+\.\d+")


@dataclass(frozen=True)
class TestResult:
    """Represents a single test run result."""
    ocp_full_version: str
    gpu_operator_version: str
    test_status: str
    prow_job_url: str
    job_timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            OCP_FULL_VERSION: self.ocp_full_version,
            GPU_OPERATOR_VERSION: self.gpu_operator_version,
            "test_status": self.test_status,
            "prow_job_url": self.prow_job_url,
            "job_timestamp": self.job_timestamp,
        }

    def build_key(self) -> Tuple[str, str, str]:
        """Get the PR number, job name and build ID for deduplication purposes."""
        repo, pr_number, job_name, build_id = extract_build_components(self.prow_job_url)
        return (pr_number, job_name, build_id)

    def has_exact_versions(self) -> bool:
        """Check if both versions are exact X.Y.Z semver (not fallbacks like '4.21' or '1.4.x')."""
        return (
            bool(SEMVER_REGEX.match(self.ocp_full_version))
            and bool(SEMVER_REGEX.match(self.gpu_operator_version))
        )



def fetch_filtered_files(pr_number: str, glob_pattern: str) -> List[Dict[str, Any]]:
    """Fetch files matching a specific glob pattern for a PR."""
    logger.info(f"Fetching files matching pattern: {glob_pattern}")

    params = {
        "prefix": f"pr-logs/pull/rh-ecosystem-edge_amd-ci/{pr_number}/",
        "alt": "json",
        "matchGlob": glob_pattern,
        "maxResults": str(GCS_MAX_RESULTS_PER_REQUEST),
        "projection": "noAcl",
    }
    headers = {"Accept": "application/json"}

    all_items = []
    next_page_token = None

    while True:
        if next_page_token:
            params["pageToken"] = next_page_token

        response_data = http_get_json(
            GCS_API_BASE_URL, params=params, headers=headers)
        items = response_data.get("items", [])
        all_items.extend(items)

        next_page_token = response_data.get("nextPageToken")
        if not next_page_token:
            break

    logger.info(f"Found {len(all_items)} files matching {glob_pattern}")
    return all_items


def fetch_pr_files(pr_number: str) -> List[Dict[str, Any]]:
    """Fetch all finished.json files for a PR."""
    logger.info(f"Fetching files for PR #{pr_number}")
    all_finished_files = fetch_filtered_files(pr_number, "**/finished.json")
    return all_finished_files


def extract_build_components(path: str) -> Tuple[str, str, str, str]:
    """Extract build components (repo, pr_number, job_name, build_id) from URL or file path."""
    original_path = path
    if '/artifacts/' in path:
        path = path.split('/artifacts/')[0] + '/'

    match = TEST_RESULT_PATH_REGEX.search(path)
    if not match:
        msg = "AMD GPU operator path regex mismatch" if "e2e-amd-ci" in original_path else "Unexpected path format"
        raise ValueError(msg)

    repo = match.group("repo")
    pr_number = match.group("pr_number")
    job_name = match.group("job_name")
    build_id = match.group("build_id")

    return (repo, pr_number, job_name, build_id)


def filter_e2e_finished_files(all_finished_files: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str, str], Dict[str, Dict[str, Any]]]]:
    """Filter AMD GPU operator E2E finished.json files, preferring nested when available.

    Nested path: artifacts/e2e-amd-ci/amd-gpu-operator-test/finished.json
    Top-level: <build_id>/finished.json (no /artifacts/ in path)
    """
    preferred_files = {}
    all_build_files = {}

    for file_item in all_finished_files:
        path = file_item.get("name", "")

        if not ("e2e-amd-ci" in path and path.endswith('/finished.json')):
            continue

        is_nested = '/artifacts/e2e-amd-ci/amd-gpu-operator-test/finished.json' in path
        is_top_level = not is_nested and '/artifacts/' not in path

        if not (is_nested or is_top_level):
            continue

        try:
            repo, pr_number, job_name, build_id = extract_build_components(path)
            build_key = (pr_number, job_name, build_id)
        except ValueError:
            continue

        if build_key not in all_build_files:
            all_build_files[build_key] = {}

        if is_nested:
            all_build_files[build_key]['nested'] = file_item
        else:
            all_build_files[build_key]['top_level'] = file_item

        if build_key not in preferred_files or is_nested:
            preferred_files[build_key] = (file_item, is_nested)

    result = [file_item for file_item, _ in preferred_files.values()]
    dual_builds = {k: v for k, v in all_build_files.items()
                   if 'nested' in v and 'top_level' in v}

    return result, dual_builds


def build_files_lookup(
    finished_files: List[Dict[str, Any]],
) -> Tuple[Dict[Tuple[str, str, str], Dict[str, Dict[str, Any]]], Set[Tuple[str, str, str]]]:
    """Build a lookup dictionary mapping build keys to their finished.json files."""
    build_files = {}
    all_builds = set()

    for file_item in finished_files:
        path = file_item.get("name", "")

        try:
            repo, pr_number, job_name, build_id = extract_build_components(path)
        except ValueError:
            continue

        if build_id in ['latest-build.txt', 'latest-build']:
            continue

        key = (pr_number, job_name, build_id)

        if key not in build_files:
            build_files[key] = {}

        build_files[key]['finished'] = file_item
        all_builds.add(key)

    return build_files, all_builds


GPU_VERSION_RESOLVED_REGEX = re.compile(r"Resolved AMD GPU Operator \S+ -> (\S+)")


def format_gpu_version(gpu_suffix: str) -> str:
    """Convert GPU version suffix from job name to display format.

    '1-3-x' -> '1.3.x'
    'master' -> 'master'
    """
    if gpu_suffix == "master":
        return "master"
    return gpu_suffix.replace("-", ".")


def fetch_exact_ocp_version(build_base_path: str) -> Optional[str]:
    """Fetch the exact OCP version from the release-images-latest artifact.

    The file is an ImageStream JSON with metadata.name containing the full version (e.g. '4.20.17').
    """
    release_path = f"{build_base_path}/artifacts/release/artifacts/release-images-latest"
    try:
        content = fetch_gcs_file_content(release_path)
        data = json.loads(content)
        return data.get("metadata", {}).get("name")
    except Exception as e:
        logger.warning(f"Could not fetch exact OCP version from {release_path}: {e}")
        return None


def fetch_exact_gpu_version(build_base_path: str, e2e_step_name: str) -> Optional[str]:
    """Fetch the exact GPU operator version from the install-operators build log.

    Parses the line: 'Resolved AMD GPU Operator X.Y -> X.Y.Z'
    """
    log_path = f"{build_base_path}/artifacts/{e2e_step_name}/amd-gpu-operator-install-operators/build-log.txt"
    try:
        content = fetch_gcs_file_content(log_path)
        match = GPU_VERSION_RESOLVED_REGEX.search(content)
        if match:
            return match.group(1)
        logger.warning(f"Could not parse GPU operator version from {log_path}")
        return None
    except Exception as e:
        logger.warning(f"Could not fetch GPU operator version from {log_path}: {e}")
        return None


def get_build_base_path(finished_file_path: str) -> str:
    """Get the base build path (up to build_id) from a finished.json path."""
    if '/artifacts/' in finished_file_path:
        return finished_file_path.split('/artifacts/')[0]
    return finished_file_path[:-len('/finished.json')]


def process_single_build(
    pr_number_arg: str,
    job_name: str,
    build_id: str,
    ocp_version: str,
    gpu_suffix: str,
    build_files: Dict[Tuple[str, str, str], Dict[str, Dict[str, Any]]],
    dual_builds_info: Optional[Dict[Tuple[str, str, str], Dict[str, Dict[str, Any]]]] = None
) -> TestResult:
    """Process a single build and return its test result."""
    key = (pr_number_arg, job_name, build_id)
    build_file_set = build_files[key]

    finished_file = build_file_set['finished']
    finished_content = fetch_gcs_file_content(finished_file['name'])
    finished_data = json.loads(finished_content)
    status = finished_data["result"]
    timestamp = finished_data["timestamp"]

    if dual_builds_info and key in dual_builds_info:
        dual_files = dual_builds_info[key]
        if 'nested' in dual_files and 'top_level' in dual_files:
            nested_content = fetch_gcs_file_content(dual_files['nested']['name'])
            nested_data = json.loads(nested_content)
            nested_status = nested_data["result"]

            top_level_content = fetch_gcs_file_content(dual_files['top_level']['name'])
            top_level_data = json.loads(top_level_content)
            top_level_status = top_level_data["result"]

            if nested_status == STATUS_SUCCESS and top_level_status != STATUS_SUCCESS:
                logger.warning(
                    f"Build {build_id}: GPU operator tests SUCCEEDED but overall build has finished with status {top_level_status}."
                )

    job_url = build_prow_job_url(finished_file['name'])
    logger.info(f"Built prow job URL for build {build_id} from path {finished_file['name']}: {job_url}")

    build_base_path = get_build_base_path(finished_file['name'])
    display_ocp = ocp_version
    display_gpu = format_gpu_version(gpu_suffix)

    if gpu_suffix != "master":
        exact_ocp = fetch_exact_ocp_version(build_base_path)
        if exact_ocp:
            display_ocp = exact_ocp
            logger.info(f"Resolved exact OCP version: {exact_ocp}")

        e2e_step_name = "e2e-amd-ci-" + gpu_suffix
        exact_gpu = fetch_exact_gpu_version(build_base_path, e2e_step_name)
        if exact_gpu:
            display_gpu = exact_gpu
            logger.info(f"Resolved exact GPU operator version: {exact_gpu}")

    result = TestResult(display_ocp, display_gpu, status, job_url, str(timestamp))

    return result


def process_tests_for_pr(pr_number: str, results_by_ocp: Dict[str, Dict[str, Any]]) -> None:
    """Retrieve and store test results for all jobs under a single PR."""
    logger.info(f"Fetching test data for PR #{pr_number}")

    all_finished_files = fetch_pr_files(pr_number)

    finished_files, dual_builds_info = filter_e2e_finished_files(all_finished_files)

    build_files, all_builds = build_files_lookup(finished_files)

    logger.info(f"Found {len(all_builds)} builds to process")

    processed_count = 0

    for pr_num, job_name, build_id in sorted(all_builds):
        if job_name.startswith("rehearse-"):
            repo = "openshift_release"
        else:
            repo = "rh-ecosystem-edge_amd-ci"

        job_path = f"pr-logs/pull/{repo}/{pr_num}/{job_name}/"
        full_path = f"{job_path}{build_id}"
        match = TEST_RESULT_PATH_REGEX.search(full_path)
        if not match:
            logger.warning(f"Could not parse versions from components: {pr_num}, {job_name}, {build_id}")
            continue
        ocp_version = match.group("ocp_version")
        gpu_suffix = match.group("gpu_version") or "master"

        logger.info(
            f"Processing build {build_id} for {ocp_version} + {gpu_suffix}")

        result = process_single_build(
            pr_num, job_name, build_id, ocp_version, gpu_suffix, build_files, dual_builds_info)

        results_by_ocp.setdefault(ocp_version, {"bundle_tests": [], "release_tests": [], "job_history_links": set()})

        job_history_url = f"https://prow.ci.openshift.org/job-history/gs/test-platform-results/pr-logs/directory/{job_name}"
        results_by_ocp[ocp_version]["job_history_links"].add(job_history_url)

        # Jobs ending with a version suffix (e.g. -1-3-x, -1-4-x) are release tests;
        # jobs without a version suffix (bare e2e-amd-ci) are bundle/master tests
        if gpu_suffix == "master":
            results_by_ocp[ocp_version]["bundle_tests"].append(result.to_dict())
        else:
            if result.has_exact_versions() and result.test_status != STATUS_ABORTED:
                results_by_ocp[ocp_version]["release_tests"].append(result.to_dict())
            else:
                logger.debug(
                    f"Excluded release test for build {build_id}: "
                    f"status={result.test_status}, exact_versions={result.has_exact_versions()}")

        processed_count += 1

    logger.info(f"Processed {processed_count} builds for PR #{pr_number}")


def process_closed_prs(results_by_ocp: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> None:
    """Retrieve and store test results for all closed PRs against the main branch."""
    logger.info("Retrieving PR history...")
    url = "https://api.github.com/repos/rh-ecosystem-edge/amd-ci/pulls"
    params = {"state": "closed", "base": "main",
              "per_page": "100", "page": "1"}
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    response_data = http_get_json(url, params=params, headers=headers)
    for pr in response_data:
        pr_number = str(pr["number"])
        logger.info(f"Processing PR #{pr_number}")
        process_tests_for_pr(pr_number, results_by_ocp)


def merge_bundle_tests(
    new_tests: List[Dict[str, Any]],
    existing_tests: List[Dict[str, Any]],
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Merge bundle tests with existing bundle tests and apply limit while keeping the most recent results."""
    all_tests_by_build = {}

    for item in existing_tests:
        result = TestResult(**item)
        build_key = result.build_key()
        all_tests_by_build[build_key] = item

    for item in new_tests:
        result = TestResult(**item)
        build_key = result.build_key()
        all_tests_by_build[build_key] = item

    all_tests = list(all_tests_by_build.values())
    all_tests.sort(key=lambda x: int(x.get('job_timestamp', '0')), reverse=True)

    if limit is not None:
        return all_tests[:limit]

    return all_tests


def get_version_key(result: TestResult) -> Tuple[str, str]:
    """Get the version combination key (OCP, GPU operator) for grouping."""
    return (result.ocp_full_version, result.gpu_operator_version.split("(")[0].strip())


def merge_release_tests(
    new_tests: List[Dict[str, Any]],
    existing_tests: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Merge release tests keeping one result per version combination.

    Groups by (OCP version, GPU operator version) and keeps the best result
    for each combination. Prefers SUCCESS over other statuses, then latest timestamp.
    """
    results_by_version = {}

    for item in existing_tests:
        result = TestResult(**item)
        version_key = get_version_key(result)
        results_by_version.setdefault(version_key, []).append(result)

    for item in new_tests:
        result = TestResult(**item)
        if result.has_exact_versions() and result.test_status != STATUS_ABORTED:
            version_key = get_version_key(result)
            results_by_version.setdefault(version_key, []).append(result)

    final_results = []
    for version_results in results_by_version.values():
        success_results = [r for r in version_results if r.test_status == STATUS_SUCCESS]
        other_results = [r for r in version_results if r.test_status != STATUS_SUCCESS]

        selected_result = None
        if success_results:
            success_results.sort(key=lambda x: int(x.job_timestamp), reverse=True)
            selected_result = success_results[0]
        elif other_results:
            other_results.sort(key=lambda x: int(x.job_timestamp), reverse=True)
            selected_result = other_results[0]

        if selected_result:
            final_results.append(selected_result.to_dict())

    final_results.sort(key=lambda x: int(x.get('job_timestamp', '0')), reverse=True)

    return final_results


def merge_ocp_version_results(
    new_version_data: Dict[str, List[Dict[str, Any]]],
    existing_version_data: Dict[str, Any],
    bundle_result_limit: Optional[int] = None
) -> Dict[str, Any]:
    """Merge results for a single OCP version."""
    merged_version_data = {"notes": [], "bundle_tests": [], "release_tests": [], "job_history_links": []}
    merged_version_data.update(existing_version_data)

    new_bundle_tests = new_version_data.get("bundle_tests", [])
    existing_bundle_tests = merged_version_data.get("bundle_tests", [])
    merged_version_data["bundle_tests"] = merge_bundle_tests(
        new_bundle_tests, existing_bundle_tests, bundle_result_limit
    )

    new_release_tests = new_version_data.get("release_tests", [])
    existing_release_tests = merged_version_data.get("release_tests", [])
    merged_version_data["release_tests"] = merge_release_tests(
        new_release_tests, existing_release_tests
    )

    new_job_history_links = new_version_data.get("job_history_links", set())
    existing_job_history_links = merged_version_data.get("job_history_links", [])

    all_job_history_links = set(existing_job_history_links)
    all_job_history_links.update(new_job_history_links)
    merged_version_data["job_history_links"] = sorted(list(all_job_history_links))

    return merged_version_data


def merge_and_save_results(
    new_results: Dict[str, Dict[str, List[Dict[str, Any]]]],
    output_file: str,
    existing_results: Dict[str, Dict[str, Any]] = None,
    bundle_result_limit: Optional[int] = None
) -> None:
    """Merge and save test results with separated bundle and release test keys."""
    merged_results = existing_results.copy() if existing_results else {}

    for ocp_version, version_data in new_results.items():
        existing_version_data = merged_results.get(ocp_version, {})
        merged_version_data = merge_ocp_version_results(
            version_data, existing_version_data, bundle_result_limit
        )
        merged_results[ocp_version] = merged_version_data

    with open(output_file, "w") as f:
        json.dump(merged_results, f, indent=4)

    logger.info(f"Results saved to {output_file}")


# =============================================================================
# Main Workflow: Update JSON
# =============================================================================

def int_or_none(value: Optional[str]) -> Optional[int]:
    """Convert string to int or None for unlimited."""
    if value is None:
        return None
    if value.lower() in ('none', 'unlimited'):
        return None
    return int(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Matrix Utility")
    parser.add_argument("--pr_number", default="all",
                        help="PR number to process; use 'all' for full history")
    parser.add_argument("--baseline_data_filepath", required=True,
                        help="Path to the baseline data file")
    parser.add_argument("--merged_data_filepath", required=True,
                        help="Path to the updated (merged) data file")
    parser.add_argument("--bundle_result_limit", type=int_or_none, default=None,
                        help="Number of latest bundle results to keep per version. Omit or use 'unlimited' for no limit.")
    args = parser.parse_args()

    with open(args.baseline_data_filepath, "r") as f:
        existing_results: Dict[str, Dict[str, Any]] = json.load(f)
    logger.info(f"Loaded baseline data with {len(existing_results)} OCP versions")

    local_results: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    if args.pr_number.lower() == "all":
        process_closed_prs(local_results)
    else:
        process_tests_for_pr(args.pr_number, local_results)
    merge_and_save_results(
        local_results, args.merged_data_filepath, existing_results=existing_results, bundle_result_limit=args.bundle_result_limit)


if __name__ == "__main__":
    main()
