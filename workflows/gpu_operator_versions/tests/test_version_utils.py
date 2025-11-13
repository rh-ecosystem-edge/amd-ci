#!/usr/bin/env python

import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from workflows.gpu_operator_versions.version_utils import (
    max_version, get_latest_versions, get_earliest_versions, get_sorted_versions
)


class TestVersionUtils(unittest.TestCase):
    """Test cases for version utility functions."""

    def test_max_version(self):
        """Test max_version function."""
        self.assertEqual(max_version('1.0.0', '1.0.1'), '1.0.1')
        self.assertEqual(max_version('1.1.0', '1.0.9'), '1.1.0')
        self.assertEqual(max_version('2.0.0', '1.9.9'), '2.0.0')
        self.assertEqual(max_version('1.0.0', '1.0.0'), '1.0.0')

    def test_get_sorted_versions(self):
        """Test get_sorted_versions function."""
        versions = ['4.12', '4.10', '4.15', '4.11']
        expected = ['4.10', '4.11', '4.12', '4.15']
        self.assertEqual(get_sorted_versions(versions), expected)


class TestGetLatestVersions(unittest.TestCase):
    """Test cases for get_latest_versions function."""

    def test_empty(self):
        """Test with empty list."""
        versions = []
        self.assertEqual(get_latest_versions(versions, 2), [])

    def test_less_than_count(self):
        """Test when list has fewer items than requested count."""
        versions = ['1.1']
        self.assertEqual(get_latest_versions(versions, 2), ['1.1'])

    def test_exact_count(self):
        """Test when list matches requested count."""
        versions = ['1.1', '1.2']
        self.assertEqual(get_latest_versions(versions, 2), ['1.1', '1.2'])

    def test_more_than_count(self):
        """Test when list has more items than requested count."""
        versions = ['1.1', '1.3', '1.2']
        self.assertEqual(get_latest_versions(versions, 2), ['1.2', '1.3'])

    def test_count_one(self):
        """Test getting only the latest version."""
        versions = ['1.1', '1.3', '1.2']
        self.assertEqual(get_latest_versions(versions, 1), ['1.3'])

    def test_reverse_order(self):
        """Test proper sorting of reverse-ordered input."""
        versions = ['1.3', '1.2', '1.1']
        self.assertEqual(get_latest_versions(versions, 3), ['1.1', '1.2', '1.3'])

    def test_get_latest_versions_comprehensive(self):
        """Test comprehensive scenarios for get_latest_versions."""
        versions = ['4.10', '4.11', '4.12', '4.13', '4.14']
        
        result = get_latest_versions(versions, 2)
        self.assertEqual(result, ['4.13', '4.14'])
        
        result = get_latest_versions(versions, 1)
        self.assertEqual(result, ['4.14'])
        
        result = get_latest_versions(versions, 10)
        self.assertEqual(result, versions)

    def test_count_zero_raises_error(self):
        """Test that count=0 raises ValueError."""
        versions = ['1.1', '1.2', '1.3']
        with self.assertRaises(ValueError) as context:
            get_latest_versions(versions, 0)
        self.assertEqual(str(context.exception), "count must be positive")

    def test_count_negative_raises_error(self):
        """Test that negative count raises ValueError."""
        versions = ['1.1', '1.2', '1.3']
        with self.assertRaises(ValueError) as context:
            get_latest_versions(versions, -1)
        self.assertEqual(str(context.exception), "count must be positive")


class TestGetEarliestVersions(unittest.TestCase):
    """Test cases for get_earliest_versions function."""

    def test_empty(self):
        """Test with empty list."""
        versions = []
        self.assertEqual(get_earliest_versions(versions, 2), [])

    def test_less_than_count(self):
        """Test when list has fewer items than requested count."""
        versions = ['1.1']
        self.assertEqual(get_earliest_versions(versions, 2), ['1.1'])

    def test_exact_count(self):
        """Test when list matches requested count."""
        versions = ['1.2', '1.1']
        self.assertEqual(get_earliest_versions(versions, 2), ['1.1', '1.2'])

    def test_more_than_count(self):
        """Test when list has more items than requested count."""
        versions = ['1.2', '1.3', '1.1']
        self.assertEqual(get_earliest_versions(versions, 2), ['1.1', '1.2'])

    def test_count_one(self):
        """Test getting only the earliest version."""
        versions = ['1.3', '1.1', '1.2']
        self.assertEqual(get_earliest_versions(versions, 1), ['1.1'])

    def test_reverse_order(self):
        """Test proper sorting of reverse-ordered input."""
        versions = ['1.3', '1.2', '1.1']
        self.assertEqual(get_earliest_versions(versions, 3), ['1.1', '1.2', '1.3'])

    def test_get_earliest_versions_comprehensive(self):
        """Test comprehensive scenarios for get_earliest_versions."""
        versions = ['4.10', '4.11', '4.12', '4.13', '4.14']
        
        result = get_earliest_versions(versions, 2)
        self.assertEqual(result, ['4.10', '4.11'])
        
        result = get_earliest_versions(versions, 1)
        self.assertEqual(result, ['4.10'])
        
        result = get_earliest_versions(versions, 10)
        self.assertEqual(result, versions)

    def test_count_zero_raises_error(self):
        """Test that count=0 raises ValueError."""
        versions = ['1.1', '1.2', '1.3']
        with self.assertRaises(ValueError) as context:
            get_earliest_versions(versions, 0)
        self.assertEqual(str(context.exception), "count must be positive")

    def test_count_negative_raises_error(self):
        """Test that negative count raises ValueError."""
        versions = ['1.1', '1.2', '1.3']
        with self.assertRaises(ValueError) as context:
            get_earliest_versions(versions, -1)
        self.assertEqual(str(context.exception), "count must be positive")


if __name__ == '__main__':
    unittest.main()

