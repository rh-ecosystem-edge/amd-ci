#!/usr/bin/env python

import json
import os
import tempfile
import unittest

from workflows.common.broken_versions import (
    _version_matches,
    find_broken_entry,
    format_broken_note,
    is_broken,
    load_broken_versions,
    notes_for_ocp_key,
)


class TestVersionMatches(unittest.TestCase):
    """Test cases for the dot-segment prefix matching used to compare version specs."""

    def test_major_spec_matches_patch_version(self):
        self.assertTrue(_version_matches("4", "4.21.25"))

    def test_minor_spec_matches_patch_version(self):
        self.assertTrue(_version_matches("4.21", "4.21.25"))

    def test_exact_patch_spec_matches_same_patch(self):
        self.assertTrue(_version_matches("4.21.25", "4.21.25"))

    def test_exact_patch_spec_does_not_match_other_patch(self):
        self.assertFalse(_version_matches("4.21.25", "4.21.26"))

    def test_spec_is_not_a_raw_string_prefix(self):
        """"4.2" must not match "4.20" - segments are compared whole, not as raw substrings."""
        self.assertFalse(_version_matches("4.2", "4.20"))

    def test_minor_spec_does_not_match_different_minor(self):
        self.assertFalse(_version_matches("4.21", "4.22.1"))


class TestFindBrokenEntry(unittest.TestCase):
    """Test cases for find_broken_entry / is_broken combination matching."""

    def test_exact_combo_match(self):
        entries = [{"ocp_version": "4.21", "gpu_operator_version": "1.4.1", "reason": "r"}]
        self.assertTrue(is_broken("4.21.25", "1.4.1", entries))

    def test_exact_combo_no_match_different_gpu(self):
        entries = [{"ocp_version": "4.21", "gpu_operator_version": "1.4.1", "reason": "r"}]
        self.assertFalse(is_broken("4.21.25", "1.4.2", entries))

    def test_gpu_only_wildcard_matches_any_ocp(self):
        entries = [{"gpu_operator_version": "1.3", "reason": "r"}]
        self.assertTrue(is_broken("4.16.1", "1.3.0", entries))
        self.assertTrue(is_broken("4.22.9", "1.3.9", entries))

    def test_gpu_only_wildcard_does_not_match_other_gpu(self):
        entries = [{"gpu_operator_version": "1.3", "reason": "r"}]
        self.assertFalse(is_broken("4.16.1", "1.4.0", entries))

    def test_ocp_only_wildcard_matches_any_gpu(self):
        entries = [{"ocp_version": "4.16.66", "reason": "r"}]
        self.assertTrue(is_broken("4.16.66", "1.4.1", entries))
        self.assertTrue(is_broken("4.16.66", "1.5.0", entries))

    def test_ocp_only_wildcard_does_not_match_other_patch(self):
        entries = [{"ocp_version": "4.16.66", "reason": "r"}]
        self.assertFalse(is_broken("4.16.67", "1.4.1", entries))

    def test_major_level_ban_persists_across_patches(self):
        entries = [{"gpu_operator_version": "1", "reason": "r"}]
        self.assertTrue(is_broken("4.20.1", "1.0.0", entries))
        self.assertTrue(is_broken("4.20.1", "1.9.9", entries))

    def test_no_match_when_no_entries(self):
        self.assertFalse(is_broken("4.21.25", "1.4.1", []))

    def test_no_match_when_candidate_missing_specified_field(self):
        """An entry specifying a field never matches a candidate that lacks a value for it."""
        entries = [{"gpu_operator_version": "1.4.1", "reason": "r"}]
        self.assertFalse(is_broken("4.21.25", None, entries))

    def test_find_broken_entry_returns_the_matching_entry(self):
        entries = [{"ocp_version": "4.21", "gpu_operator_version": "1.4.1", "reason": "known bug"}]
        found = find_broken_entry("4.21.25", "1.4.1", entries)
        self.assertEqual(found["reason"], "known bug")

    def test_find_broken_entry_returns_none_when_no_match(self):
        entries = [{"ocp_version": "4.21", "gpu_operator_version": "1.4.1", "reason": "r"}]
        self.assertIsNone(find_broken_entry("4.22.1", "1.4.1", entries))


class TestLoadBrokenVersions(unittest.TestCase):
    """Test cases for load_broken_versions, including validation of hand-edited entries."""

    def test_missing_path_returns_empty_list(self):
        self.assertEqual(load_broken_versions(None), [])

    def test_nonexistent_file_returns_empty_list(self):
        self.assertEqual(load_broken_versions("/nonexistent/path/broken_versions.json"), [])

    def test_loads_valid_entries(self):
        entries = [{"ocp_version": "4.21", "gpu_operator_version": "1.4.1", "reason": "r"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(entries, f)
            path = f.name
        try:
            self.assertEqual(load_broken_versions(path), entries)
        finally:
            os.remove(path)

    def test_raises_on_entry_missing_reason(self):
        entries = [{"ocp_version": "4.21"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(entries, f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_broken_versions(path)
        finally:
            os.remove(path)

    def test_raises_on_entry_with_no_version_fields(self):
        entries = [{"reason": "this would ban everything"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(entries, f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_broken_versions(path)
        finally:
            os.remove(path)

    def test_raises_when_file_is_not_a_list(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"not": "a list"}, f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_broken_versions(path)
        finally:
            os.remove(path)

    def _assert_entries_raise(self, entries):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(entries, f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_broken_versions(path)
        finally:
            os.remove(path)

    def test_raises_on_non_dict_entry(self):
        self._assert_entries_raise(["4.21"])

    def test_raises_on_null_entry(self):
        self._assert_entries_raise([None])

    def test_raises_on_non_string_ocp_version(self):
        self._assert_entries_raise([{"ocp_version": 421, "reason": "r"}])

    def test_raises_on_non_string_gpu_version(self):
        self._assert_entries_raise([{"gpu_operator_version": 141, "reason": "r"}])

    def test_raises_on_empty_string_version(self):
        self._assert_entries_raise([{"ocp_version": "", "reason": "r"}])

    def test_raises_on_non_string_reason(self):
        self._assert_entries_raise([{"ocp_version": "4.21", "reason": 123}])

    def test_raises_on_empty_string_reason(self):
        self._assert_entries_raise([{"ocp_version": "4.21", "reason": "   "}])


class TestFormatBrokenNote(unittest.TestCase):
    """Test cases for formatting a broken version entry as a dashboard note."""

    def test_combo_entry(self):
        entry = {"ocp_version": "4.21", "gpu_operator_version": "1.4.1", "reason": "installs fails"}
        self.assertEqual(
            format_broken_note(entry),
            "GPU Operator 1.4.1 is broken on OpenShift 4.21: installs fails",
        )

    def test_gpu_only_entry(self):
        entry = {"gpu_operator_version": "1.3", "reason": "KMM incompatibility"}
        self.assertEqual(
            format_broken_note(entry),
            "GPU Operator 1.3 is broken on all OpenShift versions: KMM incompatibility",
        )

    def test_ocp_only_entry(self):
        entry = {"ocp_version": "4.16.66", "reason": "kernel regression"}
        self.assertEqual(
            format_broken_note(entry),
            "All GPU Operator versions are broken on OpenShift 4.16.66: kernel regression",
        )


class TestNotesForOcpKey(unittest.TestCase):
    """Test cases for mapping broken entries to per-OCP-version dashboard notes."""

    def test_minor_bucket_matches_minor_entry(self):
        entries = [{"ocp_version": "4.21", "gpu_operator_version": "1.4.1", "reason": "r"}]
        self.assertEqual(len(notes_for_ocp_key("4.21", entries)), 1)

    def test_minor_bucket_matches_patch_level_entry(self):
        entries = [{"ocp_version": "4.16.66", "reason": "r"}]
        self.assertEqual(len(notes_for_ocp_key("4.16", entries)), 1)

    def test_minor_bucket_matches_major_level_entry(self):
        entries = [{"ocp_version": "4", "gpu_operator_version": "1.3", "reason": "r"}]
        self.assertEqual(len(notes_for_ocp_key("4.21", entries)), 1)

    def test_minor_bucket_does_not_match_other_minor_entry(self):
        entries = [{"ocp_version": "4.20", "gpu_operator_version": "1.4.1", "reason": "r"}]
        self.assertEqual(notes_for_ocp_key("4.21", entries), [])

    def test_gpu_wildcard_entry_applies_to_every_bucket(self):
        entries = [{"gpu_operator_version": "1.3", "reason": "r"}]
        self.assertEqual(len(notes_for_ocp_key("4.16", entries)), 1)
        self.assertEqual(len(notes_for_ocp_key("4.22", entries)), 1)

    def test_no_matching_entries_returns_empty_list(self):
        self.assertEqual(notes_for_ocp_key("4.21", []), [])


if __name__ == '__main__':
    unittest.main()
