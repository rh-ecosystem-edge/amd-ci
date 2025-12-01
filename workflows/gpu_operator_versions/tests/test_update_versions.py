#!/usr/bin/env python

import copy
import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from workflows.gpu_operator_versions.update_versions import (
    calculate_diffs, version2suffix, create_tests_matrix
)

base_versions = {
    'gpu-main-latest': 'A',
    'gpu-operator': {
        '1.4': '1.4.0',
        '1.5': '1.5.0'
    },
    'ocp': {
        '4.18': '4.18.1',
        '4.19': '4.19.1'
    }
}


class TestCalculateDiffs(unittest.TestCase):
    """Test cases for calculate_diffs function."""

    def test_bundle_key_created(self):
        """Test when bundle key is created from scratch."""
        old_versions = {}
        new_versions = {'gpu-main-latest': 'XYZ'}
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'gpu-main-latest': 'XYZ'})

    def test_bundle_changed(self):
        """Test when bundle SHA changes."""
        old_versions = base_versions
        new_versions = copy.deepcopy(old_versions)
        new_versions['gpu-main-latest'] = 'B'
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'gpu-main-latest': 'B'})

    def test_gpu_versions_key_created(self):
        """Test when gpu-operator key is created from scratch."""
        old_versions = {}
        new_versions = {'gpu-operator': {'1.4': '1.4.1'}}
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'gpu-operator': {'1.4': '1.4.1'}})

    def test_gpu_version_changed(self):
        """Test when a GPU operator version is updated."""
        old_versions = base_versions
        new_versions = copy.deepcopy(old_versions)
        new_versions['gpu-operator']['1.4'] = '1.4.1'
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'gpu-operator': {'1.4': '1.4.1'}})

    def test_gpu_version_added(self):
        """Test when a new GPU operator version is added."""
        old_versions = base_versions
        new_versions = copy.deepcopy(old_versions)
        new_versions['gpu-operator']['1.6'] = '1.6.0'
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'gpu-operator': {'1.6': '1.6.0'}})

    def test_gpu_version_removed(self):
        """Test when a GPU operator version is removed."""
        old_versions = base_versions
        new_versions = copy.deepcopy(old_versions)
        del new_versions['gpu-operator']['1.5']
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {})

    def test_ocp_version_key_created(self):
        """Test when ocp key is created from scratch."""
        old_versions = {}
        new_versions = {'ocp': {'4.18': '4.18.2'}}
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'ocp': {'4.18': '4.18.2'}})

    def test_ocp_version_changed(self):
        """Test when an OpenShift version is updated."""
        old_versions = base_versions
        new_versions = copy.deepcopy(old_versions)
        new_versions['ocp']['4.18'] = '4.18.2'
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'ocp': {'4.18': '4.18.2'}})

    def test_ocp_version_added(self):
        """Test when a new OpenShift version is added."""
        old_versions = base_versions
        new_versions = copy.deepcopy(old_versions)
        new_versions['ocp']['4.20'] = '4.20.0'
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {'ocp': {'4.20': '4.20.0'}})

    def test_ocp_version_removed(self):
        """Test when an OpenShift version is removed."""
        old_versions = base_versions
        new_versions = copy.deepcopy(old_versions)
        del new_versions['ocp']['4.19']
        diff = calculate_diffs(old_versions, new_versions)
        self.assertEqual(diff, {})


class TestVersion2Suffix(unittest.TestCase):
    """Test cases for version2suffix function."""

    def test_version2suffix(self):
        """Test version2suffix function."""
        self.assertEqual(version2suffix('master'), 'master')
        self.assertEqual(version2suffix('1.0.0'), '1-0-0-x')
        self.assertEqual(version2suffix('1.4.0'), '1-4-0-x')
        self.assertEqual(version2suffix('4.18.1'), '4-18-1-x')


class TestCreateTestsMatrix(unittest.TestCase):
    """Test cases for create_tests_matrix function."""

    def test_bundle_changed(self):
        """Test when main branch SHA changes."""
        diff = {'gpu-main-latest': 'B'}
        tests = create_tests_matrix(
            diff, ['4.19', '4.18', '4.20'], ['1.4', '1.5'])
        self.assertEqual(tests, {('4.20', 'master'), ('4.18', 'master')})

    def test_gpu_version_changed(self):
        """Test when a GPU operator version is updated."""
        diff = {'gpu-operator': {'1.4': '1.4.1'}}
        all_gpu_releases = ['1.4', '1.5', '1.6']
        tests = create_tests_matrix(diff, ['4.18', '4.19'], ['1.5', '1.6'], all_gpu_releases)
        # New GPU version should be tested against all OCP versions
        self.assertEqual(tests, {('4.18', '1.4'), ('4.19', '1.4')})

    def test_gpu_version_added(self):
        """Test when a new GPU operator version is added."""
        diff = {'gpu-operator': {'1.6': '1.6.0'}}
        all_gpu_releases = ['1.4', '1.5', '1.6']
        tests = create_tests_matrix(diff, ['4.18', '4.19'], ['1.5', '1.6'], all_gpu_releases)
        # New GPU version should be tested against all OCP versions
        self.assertEqual(tests, {('4.18', '1.6'), ('4.19', '1.6')})

    def test_ocp_version_changed(self):
        """Test when an OpenShift version is updated."""
        diff = {'ocp': {'4.18': '4.18.2'}}
        # Only test against latest 2 GPU versions
        tests = create_tests_matrix(diff, ['4.18', '4.19'], ['1.5', '1.6'])
        self.assertEqual(tests, {('4.18', '1.5'), ('4.18', '1.6')})

    def test_ocp_version_added(self):
        """Test when a new OpenShift version is added."""
        diff = {'ocp': {'4.20': '4.20.0'}}
        # Only test against latest 2 GPU versions
        tests = create_tests_matrix(
            diff, ['4.18', '4.19', '4.20'], ['1.5', '1.6'])
        self.assertEqual(tests, {('4.20', '1.5'), ('4.20', '1.6')})

    def test_ocp_version_added_limited_gpu_versions(self):
        """Test when a new OpenShift version is added with limited GPU versions."""
        diff = {'ocp': {'4.21': '4.21.0'}}
        # Test with only 1 GPU version (simulating GPU_VERSIONS_TO_TEST_COUNT=1)
        tests = create_tests_matrix(
            diff, ['4.18', '4.19', '4.20', '4.21'], ['1.6'])
        self.assertEqual(tests, {('4.21', '1.6')})

    def test_no_changes(self):
        """Test when there are no version changes."""
        diff = {}
        tests = create_tests_matrix(diff, ['4.18', '4.19'], ['1.4', '1.5'])
        self.assertEqual(tests, set())


if __name__ == '__main__':
    unittest.main()

