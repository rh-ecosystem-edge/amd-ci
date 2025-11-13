#!/usr/bin/env python

import json
import os
import tempfile
import unittest
from unittest import mock, TestCase

from workflows.gpu_operator_dashboard.fetch_ci_data import (
    merge_and_save_results, OCP_FULL_VERSION, GPU_OPERATOR_VERSION,
    STATUS_SUCCESS, STATUS_FAILURE, STATUS_ABORTED)

# Testing final logic of fetch_ci_data.py which stores the JSON test data


class TestSaveToJson(TestCase):
    """Test cases for AMD GPU Operator dashboard data merging and saving."""

    def setUp(self):
        # Create a temporary directory for test files
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = self.temp_dir.name
        self.test_file = "test_data.json"

    def tearDown(self):
        # Clean up the temporary directory
        self.temp_dir.cleanup()

    def test_save_new_data_to_empty_existing(self):
        """Test saving new data when existing_data is empty."""
        new_data = {
            "4.18": {
                "release_tests": [
                    {
                        OCP_FULL_VERSION: "4.18.1",
                        GPU_OPERATOR_VERSION: "1.4.0",
                        "test_status": STATUS_SUCCESS,
                        "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/123/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/456",
                        "job_timestamp": "1712345678"
                    }
                ],
                "bundle_tests": []
            }
        }
        existing_data = {}

        data_file = os.path.join(self.output_dir, self.test_file)
        merge_and_save_results(new_data, data_file, existing_data)

        # Read the saved file and verify its contents
        with open(data_file, 'r') as f:
            saved_data = json.load(f)

        # The saved data should have the separated structure
        expected_data = {
            "4.18": {
                "notes": [],
                "bundle_tests": [],
                "release_tests": [
                    {
                        OCP_FULL_VERSION: "4.18.1",
                        GPU_OPERATOR_VERSION: "1.4.0",
                        "test_status": STATUS_SUCCESS,
                        "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/123/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/456",
                        "job_timestamp": "1712345678"
                    }
                ],
                "job_history_links": []
            }
        }
        self.assertEqual(saved_data, expected_data)

    def test_merge_with_no_duplicates(self):
        """Test merging when no duplicates exist."""
        new_data = {
            "4.18": {
                "release_tests": [
                    {
                        OCP_FULL_VERSION: "4.18.1",
                        GPU_OPERATOR_VERSION: "1.4.0",
                        "test_status": STATUS_SUCCESS,
                        "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/123/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/456",
                        "job_timestamp": "1712345678"
                    }
                ],
                "bundle_tests": []
            }
        }
        existing_data = {
            "4.18": {
                "bundle_tests": [],
                "release_tests": [
                    {
                        OCP_FULL_VERSION: "4.18.2",
                        GPU_OPERATOR_VERSION: "1.5.0",
                        "test_status": STATUS_SUCCESS,
                        "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/124/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-5-0-x/457",
                        "job_timestamp": "1712345679"
                    }
                ]
            }
        }

        data_file = os.path.join(self.output_dir, self.test_file)
        merge_and_save_results(new_data, data_file, existing_data)

        # Read the saved file and verify its contents
        with open(data_file, 'r') as f:
            saved_data = json.load(f)

        # Both entries should be present
        self.assertEqual(len(saved_data["4.18"]["release_tests"]), 2)
        self.assertTrue(any(item[GPU_OPERATOR_VERSION]
                        == "1.4.0" for item in saved_data["4.18"]["release_tests"]))
        self.assertTrue(any(item[GPU_OPERATOR_VERSION]
                        == "1.5.0" for item in saved_data["4.18"]["release_tests"]))

    def test_release_version_deduplication(self):
        """Test that release tests keep only one result per version combination."""
        # Two different builds testing the same version combination
        item1 = {
            OCP_FULL_VERSION: "4.18.1",
            GPU_OPERATOR_VERSION: "1.4.0",
            "test_status": STATUS_SUCCESS,
            "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/123/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/456",
            "job_timestamp": "1712345678"
        }

        item2 = {
            OCP_FULL_VERSION: "4.18.1",  # Same version combination
            GPU_OPERATOR_VERSION: "1.4.0",  # Same version combination
            "test_status": STATUS_FAILURE,  # Different result from different build
            "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/124/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/457",
            "job_timestamp": "1712345680"  # Later timestamp
        }

        new_data = {
            "4.18": {
                "release_tests": [item1],
                "bundle_tests": []
            }
        }
        existing_data = {
            "4.18": {
                "bundle_tests": [],
                "release_tests": [item2]
            }
        }

        data_file = os.path.join(self.output_dir, self.test_file)
        merge_and_save_results(new_data, data_file, existing_data)

        with open(data_file, 'r') as f:
            saved_data = json.load(f)

        # Only one entry should be present per version combination (SUCCESS preferred)
        self.assertEqual(len(saved_data["4.18"]["release_tests"]), 1)
        self.assertEqual(saved_data["4.18"]["release_tests"][0]["test_status"], STATUS_SUCCESS)

    def test_different_ocp_keys(self):
        """Test merging data with different OCP keys."""
        new_data = {
            "4.18": {
                "release_tests": [
                    {
                        OCP_FULL_VERSION: "4.18.1",
                        GPU_OPERATOR_VERSION: "1.4.0",
                        "test_status": STATUS_SUCCESS,
                        "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/123/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/456",
                        "job_timestamp": "1712345678"
                    }
                ],
                "bundle_tests": []
            }
        }
        existing_data = {
            "4.19": {
                "bundle_tests": [],
                "release_tests": [
                    {
                        OCP_FULL_VERSION: "4.19.5",
                        GPU_OPERATOR_VERSION: "1.4.0",
                        "test_status": STATUS_SUCCESS,
                        "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/124/pull-ci-rh-ecosystem-edge-amd-ci-main-4.19-stable-amd-gpu-operator-e2e-1-4-0-x/457",
                        "job_timestamp": "1712345679"
                    }
                ]
            }
        }

        data_file = os.path.join(self.output_dir, self.test_file)
        merge_and_save_results(new_data, data_file, existing_data)

        with open(data_file, 'r') as f:
            saved_data = json.load(f)

        # Both OCP keys should be present
        self.assertIn("4.18", saved_data)
        self.assertIn("4.19", saved_data)
        self.assertEqual(len(saved_data["4.18"]["release_tests"]), 1)
        self.assertEqual(len(saved_data["4.19"]["release_tests"]), 1)

    def test_release_success_preference(self):
        """Test that SUCCESS is preferred over FAILURE for same version combination."""
        new_item = {
            OCP_FULL_VERSION: "4.18.1",
            GPU_OPERATOR_VERSION: "1.4.0",
            "test_status": STATUS_SUCCESS,
            "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/123/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/456",
            "job_timestamp": "1712345678"
        }

        existing_item = {
            OCP_FULL_VERSION: "4.18.1",  # Same version combination
            GPU_OPERATOR_VERSION: "1.4.0",  # Same version combination
            "test_status": STATUS_FAILURE,  # Different test_status from different build
            "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/124/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/457",
            "job_timestamp": "1712345680"  # Different timestamp
        }

        new_data = {
            "4.18": {
                "release_tests": [new_item],
                "bundle_tests": []
            }
        }
        existing_data = {
            "4.18": {
                "bundle_tests": [],
                "release_tests": [existing_item]
            }
        }

        data_file = os.path.join(self.output_dir, self.test_file)
        merge_and_save_results(new_data, data_file, existing_data)

        with open(data_file, 'r') as f:
            saved_data = json.load(f)

        # Only one entry should remain (SUCCESS should be preferred over FAILURE)
        self.assertEqual(len(saved_data["4.18"]["release_tests"]), 1)
        self.assertEqual(saved_data["4.18"]["release_tests"][0]["test_status"], STATUS_SUCCESS)

    def test_bundle_tests_chronological_merge(self):
        """Test that bundle tests preserve all results chronologically."""
        existing_item = {
            OCP_FULL_VERSION: "4.18.1",
            GPU_OPERATOR_VERSION: "master",
            "test_status": STATUS_SUCCESS,
            "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/123/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-master/456",
            "job_timestamp": "1712345678"
        }
        new_item = {
            OCP_FULL_VERSION: "4.18.1",
            GPU_OPERATOR_VERSION: "master",
            "test_status": STATUS_FAILURE,  # Different build result
            "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/125/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-master/458",
            "job_timestamp": "1712345680"
        }

        new_data = {
            "4.18": {
                "bundle_tests": [new_item],
                "release_tests": []
            }
        }
        existing_data = {
            "4.18": {
                "bundle_tests": [existing_item],
                "release_tests": []
            }
        }

        data_file = os.path.join(self.output_dir, self.test_file)
        merge_and_save_results(new_data, data_file, existing_data)

        with open(data_file, 'r') as f:
            saved_data = json.load(f)

        # Both bundle test results should be preserved (different builds)
        self.assertEqual(len(saved_data["4.18"]["bundle_tests"]), 2)

        # Check that both items are present
        found_existing = any(
            item["test_status"] == STATUS_SUCCESS and "456" in item["prow_job_url"]
            for item in saved_data["4.18"]["bundle_tests"]
        )
        self.assertTrue(found_existing)

        found_new = any(
            item["test_status"] == STATUS_FAILURE and "458" in item["prow_job_url"]
            for item in saved_data["4.18"]["bundle_tests"]
        )
        self.assertTrue(found_new)

    @mock.patch('json.dump')
    def test_empty_new_data(self, mock_json_dump):
        """Test with empty new_data."""
        new_data = {}
        existing_data = {
            "4.18": {
                "bundle_tests": [],
                "release_tests": [
                    {
                        OCP_FULL_VERSION: "4.18.1",
                        GPU_OPERATOR_VERSION: "1.4.0",
                        "test_status": STATUS_SUCCESS,
                        "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/123/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/456",
                        "job_timestamp": "1712345678"
                    }
                ]
            }
        }

        data_file = os.path.join(self.output_dir, self.test_file)
        merge_and_save_results(new_data, data_file, existing_data)

        # Verify json.dump was called with the correct arguments
        mock_json_dump.assert_called_once()
        args, _ = mock_json_dump.call_args
        saved_data = args[0]

        # The existing data should remain unchanged
        self.assertEqual(saved_data, existing_data)

    def test_bundle_result_limit(self):
        """Test that bundle result limit is applied correctly."""
        bundle_items = []
        for i in range(5):
            bundle_items.append({
                OCP_FULL_VERSION: "4.18.1",
                GPU_OPERATOR_VERSION: "master",
                "test_status": STATUS_SUCCESS,
                "prow_job_url": f"https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/{123+i}/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-master/{456+i}",
                "job_timestamp": str(1712345678 + i)
            })

        new_data = {
            "4.18": {
                "bundle_tests": bundle_items,
                "release_tests": []
            }
        }
        existing_data = {}

        data_file = os.path.join(self.output_dir, self.test_file)
        # Apply limit of 3 bundle results
        merge_and_save_results(new_data, data_file, existing_data, bundle_result_limit=3)

        with open(data_file, 'r') as f:
            saved_data = json.load(f)

        # Should only keep 3 most recent bundle results
        self.assertEqual(len(saved_data["4.18"]["bundle_tests"]), 3)
        # Should be sorted newest first
        timestamps = [int(item["job_timestamp"]) for item in saved_data["4.18"]["bundle_tests"]]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_release_latest_timestamp_preference(self):
        """Test that for same status, latest timestamp is preferred in release tests."""
        # Two SUCCESS results with same version combination
        older_success = {
            OCP_FULL_VERSION: "4.18.1",
            GPU_OPERATOR_VERSION: "1.4.0",
            "test_status": STATUS_SUCCESS,
            "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/123/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/456",
            "job_timestamp": "1712345678"
        }

        newer_success = {
            OCP_FULL_VERSION: "4.18.1",  # Same version combination
            GPU_OPERATOR_VERSION: "1.4.0",  # Same version combination
            "test_status": STATUS_SUCCESS,  # Same status
            "prow_job_url": "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/124/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-amd-gpu-operator-e2e-1-4-0-x/457",
            "job_timestamp": "1712345680"  # Later timestamp
        }

        new_data = {
            "4.18": {
                "release_tests": [older_success],
                "bundle_tests": []
            }
        }
        existing_data = {
            "4.18": {
                "bundle_tests": [],
                "release_tests": [newer_success]
            }
        }

        data_file = os.path.join(self.output_dir, self.test_file)
        merge_and_save_results(new_data, data_file, existing_data)

        with open(data_file, 'r') as f:
            saved_data = json.load(f)

        # Should keep only the newer SUCCESS result
        self.assertEqual(len(saved_data["4.18"]["release_tests"]), 1)
        self.assertEqual(saved_data["4.18"]["release_tests"][0]["job_timestamp"], "1712345680")


if __name__ == '__main__':
    unittest.main()

