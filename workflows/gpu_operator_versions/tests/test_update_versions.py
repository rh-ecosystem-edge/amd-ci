#!/usr/bin/env python

import copy
import unittest

from workflows.gpu_operator_versions.update_versions import (
    calculate_diffs,
    version2suffix,
    create_tests_matrix,
)


class TestCalculateDiffs(unittest.TestCase):
    """Test cases for calculate_diffs function."""

    def setUp(self):
        """Set up test fixtures with base version data."""
        self.base_versions = {
            'gpu-operator': {
                '1.4': '1.4.1',
                '1.5': '1.5.2'
            },
            'gpu-operator-pending': {
                '1.6': '1.6.0'
            },
            'ocp': {
                '4.18': '4.18.1',
                '4.19': '4.19.1'
            }
        }

    def test_calculate_diffs_gpu_operator_key_created(self):
        """Verify detection of newly created gpu-operator key."""
        old_versions = {}
        new_versions = {'gpu-operator': {'1.4': '1.4.1'}}
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'gpu-operator': {'1.4': '1.4.1'}})

    def test_calculate_diffs_gpu_version_changed(self):
        """Verify detection of GPU operator version update."""
        new_versions = copy.deepcopy(self.base_versions)
        new_versions['gpu-operator']['1.4'] = '1.4.2'
        diff = calculate_diffs(self.base_versions, new_versions)
        self.assertEqual(diff, {'gpu-operator': {'1.4': '1.4.2'}})

    def test_calculate_diffs_gpu_version_added(self):
        """Verify detection of newly added GPU operator version."""
        new_versions = copy.deepcopy(self.base_versions)
        new_versions['gpu-operator']['1.7'] = '1.7.0'
        diff = calculate_diffs(self.base_versions, new_versions)
        self.assertEqual(diff, {'gpu-operator': {'1.7': '1.7.0'}})

    def test_calculate_diffs_gpu_version_removed_no_diff(self):
        """Verify that removed versions do not appear in diff."""
        new_versions = copy.deepcopy(self.base_versions)
        del new_versions['gpu-operator']['1.5']
        diff = calculate_diffs(self.base_versions, new_versions)
        self.assertEqual(diff, {})

    def test_calculate_diffs_ocp_key_created(self):
        """Verify detection of newly created OCP key."""
        old_versions = {}
        new_versions = {'ocp': {'4.18': '4.18.2'}}
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'ocp': {'4.18': '4.18.2'}})

    def test_calculate_diffs_ocp_version_changed(self):
        """Verify detection of OCP version update."""
        new_versions = copy.deepcopy(self.base_versions)
        new_versions['ocp']['4.18'] = '4.18.2'
        diff = calculate_diffs(self.base_versions, new_versions)
        self.assertEqual(diff, {'ocp': {'4.18': '4.18.2'}})

    def test_calculate_diffs_ocp_version_added(self):
        """Verify detection of newly added OCP version."""
        new_versions = copy.deepcopy(self.base_versions)
        new_versions['ocp']['4.20'] = '4.20.0'
        diff = calculate_diffs(self.base_versions, new_versions)
        self.assertEqual(diff, {'ocp': {'4.20': '4.20.0'}})

    def test_calculate_diffs_ocp_version_removed_no_diff(self):
        """Verify that removed OCP versions do not appear in diff."""
        new_versions = copy.deepcopy(self.base_versions)
        del new_versions['ocp']['4.19']
        diff = calculate_diffs(self.base_versions, new_versions)
        self.assertEqual(diff, {})

    def test_calculate_diffs_no_changes(self):
        """Verify that identical versions produce empty diff."""
        diff = calculate_diffs(self.base_versions, self.base_versions)
        self.assertEqual(diff, {})

    def test_calculate_diffs_multiple_changes(self):
        """Verify detection of multiple simultaneous changes."""
        new_versions = copy.deepcopy(self.base_versions)
        new_versions['gpu-operator']['1.4'] = '1.4.2'
        new_versions['ocp']['4.20'] = '4.20.0'
        diff = calculate_diffs(self.base_versions, new_versions)
        expected = {
            'gpu-operator': {'1.4': '1.4.2'},
            'ocp': {'4.20': '4.20.0'}
        }
        self.assertEqual(diff, expected)


class TestVersion2Suffix(unittest.TestCase):
    """Test cases for version2suffix function."""

    def test_version2suffix_minor_version(self):
        """Verify conversion of minor version format (X.Y)."""
        self.assertEqual(version2suffix('1.0'), '1-0-x')
        self.assertEqual(version2suffix('1.4'), '1-4-x')

    def test_version2suffix_full_version(self):
        """Verify conversion of full version format (X.Y.Z)."""
        self.assertEqual(version2suffix('1.0.0'), '1-0-0-x')
        self.assertEqual(version2suffix('1.4.0'), '1-4-0-x')
        self.assertEqual(version2suffix('4.18.1'), '4-18-1-x')


class TestCreateTestsMatrix(unittest.TestCase):
    """Test cases for create_tests_matrix function.

    The create_tests_matrix function generates test combinations based on:
    - New OCP versions: tested against all specified gpu_releases
    - New GPU versions: tested against all ocp_releases (if GPU version is in gpu_releases)
    """

    def test_create_tests_matrix_gpu_version_in_releases(self):
        """Verify test generation for GPU version that is in releases list."""
        diff = {'gpu-operator': {'1.4': '1.4.1'}}
        tests = create_tests_matrix(diff, ['4.18', '4.19'], ['1.4', '1.5', '1.6'])
        self.assertEqual(tests, {('4.18', '1.4'), ('4.19', '1.4')})

    def test_create_tests_matrix_gpu_version_not_in_releases(self):
        """Verify that GPU version not in releases list is skipped."""
        diff = {'gpu-operator': {'1.3': '1.3.1'}}
        tests = create_tests_matrix(diff, ['4.18', '4.19'], ['1.4', '1.5', '1.6'])
        self.assertEqual(tests, set())

    def test_create_tests_matrix_new_gpu_version(self):
        """Verify test generation for newly added GPU version."""
        diff = {'gpu-operator': {'1.6': '1.6.0'}}
        tests = create_tests_matrix(diff, ['4.18', '4.19'], ['1.5', '1.6'])
        self.assertEqual(tests, {('4.18', '1.6'), ('4.19', '1.6')})

    def test_create_tests_matrix_ocp_version_changed(self):
        """Verify test generation for OCP version update."""
        diff = {'ocp': {'4.18': '4.18.2'}}
        tests = create_tests_matrix(diff, ['4.18', '4.19'], ['1.5', '1.6'])
        self.assertEqual(tests, {('4.18', '1.5'), ('4.18', '1.6')})

    def test_create_tests_matrix_new_ocp_version(self):
        """Verify test generation for newly added OCP version."""
        diff = {'ocp': {'4.20': '4.20.0'}}
        tests = create_tests_matrix(diff, ['4.18', '4.19', '4.20'], ['1.5', '1.6'])
        self.assertEqual(tests, {('4.20', '1.5'), ('4.20', '1.6')})

    def test_create_tests_matrix_limited_gpu_versions(self):
        """Verify test generation with limited GPU versions (GPU_VERSIONS_TO_TEST_COUNT)."""
        diff = {'ocp': {'4.21': '4.21.0'}}
        tests = create_tests_matrix(diff, ['4.18', '4.19', '4.20', '4.21'], ['1.6'])
        self.assertEqual(tests, {('4.21', '1.6')})

    def test_create_tests_matrix_both_ocp_and_gpu_changed(self):
        """Verify test generation when both OCP and GPU versions change."""
        diff = {
            'ocp': {'4.20': '4.20.0'},
            'gpu-operator': {'1.6': '1.6.1'}
        }
        tests = create_tests_matrix(diff, ['4.18', '4.19', '4.20'], ['1.5', '1.6'])
        expected = {
            ('4.20', '1.5'), ('4.20', '1.6'),  # new OCP against all GPU
            ('4.18', '1.6'), ('4.19', '1.6'),  # new GPU against all OCP
        }
        self.assertEqual(tests, expected)

    def test_create_tests_matrix_no_changes(self):
        """Verify that empty diff produces no tests."""
        diff = {}
        tests = create_tests_matrix(diff, ['4.18', '4.19'], ['1.4', '1.5'])
        self.assertEqual(tests, set())

    def test_create_tests_matrix_empty_releases(self):
        """Verify behavior with empty release lists."""
        diff = {'ocp': {'4.20': '4.20.0'}}
        tests = create_tests_matrix(diff, ['4.20'], [])
        self.assertEqual(tests, set())


if __name__ == '__main__':
    unittest.main()
