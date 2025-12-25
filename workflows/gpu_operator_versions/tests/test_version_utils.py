#!/usr/bin/env python

import unittest

from workflows.gpu_operator_versions.version_utils import (
    max_version,
    get_latest_versions,
    get_earliest_versions,
    get_sorted_versions,
)


class TestMaxVersion(unittest.TestCase):
    """Test cases for max_version function."""

    def test_max_version_returns_higher_patch(self):
        """Verify that max_version returns the version with higher patch number."""
        self.assertEqual(max_version('1.0.0', '1.0.1'), '1.0.1')

    def test_max_version_returns_higher_minor(self):
        """Verify that max_version correctly compares minor versions."""
        self.assertEqual(max_version('1.1.0', '1.0.9'), '1.1.0')

    def test_max_version_returns_higher_major(self):
        """Verify that max_version correctly compares major versions."""
        self.assertEqual(max_version('2.0.0', '1.9.9'), '2.0.0')

    def test_max_version_equal_versions(self):
        """Verify that max_version handles equal versions."""
        self.assertEqual(max_version('1.0.0', '1.0.0'), '1.0.0')


class TestGetSortedVersions(unittest.TestCase):
    """Test cases for get_sorted_versions function."""

    def test_get_sorted_versions_basic(self):
        """Verify that versions are sorted in ascending order."""
        versions = ['4.12', '4.10', '4.15', '4.11']
        expected = ['4.10', '4.11', '4.12', '4.15']
        self.assertEqual(get_sorted_versions(versions), expected)

    def test_get_sorted_versions_empty_list(self):
        """Verify that empty list returns empty list."""
        self.assertEqual(get_sorted_versions([]), [])

    def test_get_sorted_versions_single_element(self):
        """Verify that single element list is returned unchanged."""
        self.assertEqual(get_sorted_versions(['1.0']), ['1.0'])

    def test_get_sorted_versions_already_sorted(self):
        """Verify that already sorted list remains unchanged."""
        versions = ['1.0', '1.1', '1.2']
        self.assertEqual(get_sorted_versions(versions), versions)


class TestGetLatestVersions(unittest.TestCase):
    """Test cases for get_latest_versions function."""

    def test_get_latest_versions_empty_list(self):
        """Verify that empty list returns empty list."""
        self.assertEqual(get_latest_versions([], 2), [])

    def test_get_latest_versions_fewer_than_count(self):
        """Verify behavior when list has fewer items than requested count."""
        versions = ['1.1']
        self.assertEqual(get_latest_versions(versions, 2), ['1.1'])

    def test_get_latest_versions_exact_count(self):
        """Verify behavior when list matches requested count."""
        versions = ['1.1', '1.2']
        self.assertEqual(get_latest_versions(versions, 2), ['1.1', '1.2'])

    def test_get_latest_versions_more_than_count(self):
        """Verify that only the latest N versions are returned."""
        versions = ['1.1', '1.3', '1.2']
        self.assertEqual(get_latest_versions(versions, 2), ['1.2', '1.3'])

    def test_get_latest_versions_count_one(self):
        """Verify that count=1 returns only the latest version."""
        versions = ['1.1', '1.3', '1.2']
        self.assertEqual(get_latest_versions(versions, 1), ['1.3'])

    def test_get_latest_versions_preserves_sort_order(self):
        """Verify that returned versions are in sorted order."""
        versions = ['1.3', '1.2', '1.1']
        self.assertEqual(get_latest_versions(versions, 3), ['1.1', '1.2', '1.3'])

    def test_get_latest_versions_ocp_style_versions(self):
        """Verify correct handling of OCP-style version numbers."""
        versions = ['4.10', '4.11', '4.12', '4.13', '4.14']

        result = get_latest_versions(versions, 2)
        self.assertEqual(result, ['4.13', '4.14'])

        result = get_latest_versions(versions, 1)
        self.assertEqual(result, ['4.14'])

        result = get_latest_versions(versions, 10)
        self.assertEqual(result, versions)

    def test_get_latest_versions_count_zero_raises_error(self):
        """Verify that count=0 raises ValueError."""
        versions = ['1.1', '1.2', '1.3']
        with self.assertRaises(ValueError) as context:
            get_latest_versions(versions, 0)
        self.assertEqual(str(context.exception), "count must be positive")

    def test_get_latest_versions_count_negative_raises_error(self):
        """Verify that negative count raises ValueError."""
        versions = ['1.1', '1.2', '1.3']
        with self.assertRaises(ValueError) as context:
            get_latest_versions(versions, -1)
        self.assertEqual(str(context.exception), "count must be positive")


class TestGetEarliestVersions(unittest.TestCase):
    """Test cases for get_earliest_versions function."""

    def test_get_earliest_versions_empty_list(self):
        """Verify that empty list returns empty list."""
        self.assertEqual(get_earliest_versions([], 2), [])

    def test_get_earliest_versions_fewer_than_count(self):
        """Verify behavior when list has fewer items than requested count."""
        versions = ['1.1']
        self.assertEqual(get_earliest_versions(versions, 2), ['1.1'])

    def test_get_earliest_versions_exact_count(self):
        """Verify behavior when list matches requested count."""
        versions = ['1.2', '1.1']
        self.assertEqual(get_earliest_versions(versions, 2), ['1.1', '1.2'])

    def test_get_earliest_versions_more_than_count(self):
        """Verify that only the earliest N versions are returned."""
        versions = ['1.2', '1.3', '1.1']
        self.assertEqual(get_earliest_versions(versions, 2), ['1.1', '1.2'])

    def test_get_earliest_versions_count_one(self):
        """Verify that count=1 returns only the earliest version."""
        versions = ['1.3', '1.1', '1.2']
        self.assertEqual(get_earliest_versions(versions, 1), ['1.1'])

    def test_get_earliest_versions_preserves_sort_order(self):
        """Verify that returned versions are in sorted order."""
        versions = ['1.3', '1.2', '1.1']
        self.assertEqual(get_earliest_versions(versions, 3), ['1.1', '1.2', '1.3'])

    def test_get_earliest_versions_ocp_style_versions(self):
        """Verify correct handling of OCP-style version numbers."""
        versions = ['4.10', '4.11', '4.12', '4.13', '4.14']

        result = get_earliest_versions(versions, 2)
        self.assertEqual(result, ['4.10', '4.11'])

        result = get_earliest_versions(versions, 1)
        self.assertEqual(result, ['4.10'])

        result = get_earliest_versions(versions, 10)
        self.assertEqual(result, versions)

    def test_get_earliest_versions_count_zero_raises_error(self):
        """Verify that count=0 raises ValueError."""
        versions = ['1.1', '1.2', '1.3']
        with self.assertRaises(ValueError) as context:
            get_earliest_versions(versions, 0)
        self.assertEqual(str(context.exception), "count must be positive")

    def test_get_earliest_versions_count_negative_raises_error(self):
        """Verify that negative count raises ValueError."""
        versions = ['1.1', '1.2', '1.3']
        with self.assertRaises(ValueError) as context:
            get_earliest_versions(versions, -1)
        self.assertEqual(str(context.exception), "count must be positive")


if __name__ == '__main__':
    unittest.main()
