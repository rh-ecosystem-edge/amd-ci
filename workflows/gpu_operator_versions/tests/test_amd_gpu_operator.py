#!/usr/bin/env python

import unittest

from shared.amd_gpu_releases import parse_versions_from_tags


class TestParseVersionsFromTags(unittest.TestCase):
    """Test cases for parse_versions_from_tags function.

    This function parses GitHub release tags and categorizes versions as:
    - certified: versions with at least one non-zero patch release
    - pending: versions with only patch 0 (awaiting certification)
    """

    def test_parse_versions_gpu_operator_charts_format(self):
        """Verify parsing of gpu-operator-charts-vX.Y.Z tag format."""
        tags = [
            "gpu-operator-charts-v1.0.1",
            "gpu-operator-charts-v1.0.2",
            "gpu-operator-charts-v1.1.1",
        ]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"1.0": "1.0.2", "1.1": "1.1.1"})
        self.assertEqual(pending, {})

    def test_parse_versions_simple_v_prefix_format(self):
        """Verify parsing of simple vX.Y.Z tag format."""
        tags = ["v1.0.1", "v1.0.2", "v1.1.1"]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"1.0": "1.0.2", "1.1": "1.1.1"})
        self.assertEqual(pending, {})

    def test_parse_versions_no_prefix_format(self):
        """Verify parsing of X.Y.Z tag format without prefix."""
        tags = ["1.0.1", "1.1.2"]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"1.0": "1.0.1", "1.1": "1.1.2"})
        self.assertEqual(pending, {})

    def test_parse_versions_patch_zero_is_pending(self):
        """Verify that versions with only patch 0 are marked as pending."""
        tags = [
            "gpu-operator-charts-v1.0.1",  # certified (patch > 0)
            "gpu-operator-charts-v1.1.0",  # pending (only patch 0)
        ]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"1.0": "1.0.1"})
        self.assertEqual(pending, {"1.1": "1.1.0"})

    def test_parse_versions_becomes_certified_with_patch(self):
        """Verify that version becomes certified when non-zero patch is released."""
        tags = [
            "gpu-operator-charts-v1.2.0",  # initially pending
            "gpu-operator-charts-v1.2.1",  # makes 1.2 certified
        ]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"1.2": "1.2.1"})
        self.assertEqual(pending, {})

    def test_parse_versions_keeps_highest_patch(self):
        """Verify that only the highest patch version is kept for each minor."""
        tags = [
            "gpu-operator-charts-v1.0.1",
            "gpu-operator-charts-v1.0.5",
            "gpu-operator-charts-v1.0.3",
        ]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"1.0": "1.0.5"})
        self.assertEqual(pending, {})

    def test_parse_versions_skips_non_version_tags(self):
        """Verify that non-version tags are skipped."""
        tags = [
            "gpu-operator-charts-v1.0.1",
            "some-random-tag",
            "release-candidate",
            "latest",
            "gpu-operator-charts-v1.1.1",
        ]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"1.0": "1.0.1", "1.1": "1.1.1"})
        self.assertEqual(pending, {})

    def test_parse_versions_empty_tags_list(self):
        """Verify handling of empty tags list."""
        certified, pending = parse_versions_from_tags([])

        self.assertEqual(certified, {})
        self.assertEqual(pending, {})

    def test_parse_versions_mixed_tag_formats(self):
        """Verify handling of mixed tag formats in same list."""
        tags = [
            "gpu-operator-charts-v1.0.1",
            "v1.1.1",
            "1.2.1",
        ]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"1.0": "1.0.1", "1.1": "1.1.1", "1.2": "1.2.1"})
        self.assertEqual(pending, {})

    def test_parse_versions_multiple_major_versions(self):
        """Verify handling of multiple major versions."""
        tags = [
            "gpu-operator-charts-v1.0.1",
            "gpu-operator-charts-v2.0.1",
            "gpu-operator-charts-v2.1.0",  # pending (patch 0 only)
        ]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"1.0": "1.0.1", "2.0": "2.0.1"})
        self.assertEqual(pending, {"2.1": "2.1.0"})

    def test_parse_versions_all_patch_zero(self):
        """Verify that all patch-0 versions are marked as pending."""
        tags = [
            "gpu-operator-charts-v1.0.0",
            "gpu-operator-charts-v1.1.0",
            "gpu-operator-charts-v1.2.0",
        ]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {})
        self.assertEqual(pending, {"1.0": "1.0.0", "1.1": "1.1.0", "1.2": "1.2.0"})

    def test_parse_versions_high_version_numbers(self):
        """Verify handling of high version numbers."""
        tags = [
            "gpu-operator-charts-v10.20.30",
            "gpu-operator-charts-v10.20.31",
        ]
        certified, pending = parse_versions_from_tags(tags)

        self.assertEqual(certified, {"10.20": "10.20.31"})
        self.assertEqual(pending, {})


if __name__ == '__main__':
    unittest.main()
