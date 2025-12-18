#!/usr/bin/env python

import unittest
from unittest.mock import patch, MagicMock
from requests.exceptions import RequestException

from workflows.gpu_operator_versions.openshift import fetch_ocp_versions, RELEASE_URL_API


class TestFetchOCPVersions(unittest.TestCase):
    """Test cases for fetch_ocp_versions function."""

    def _create_mock_settings(self, ignored_versions="x^", timeout=30):
        """
        Create a mock Settings object for testing.

        Args:
            ignored_versions: Regex pattern for versions to ignore
            timeout: Request timeout in seconds

        Returns:
            MagicMock: Configured mock Settings object
        """
        mock_settings = MagicMock()
        mock_settings.ignored_versions = ignored_versions
        mock_settings.request_timeout_sec = timeout
        return mock_settings

    def _create_mock_response(self, json_data, raise_for_status=None):
        """
        Create a mock response object for testing.

        Args:
            json_data: Data to return from json() method
            raise_for_status: Optional exception to raise on raise_for_status()

        Returns:
            MagicMock: Configured mock response object
        """
        mock_response = MagicMock()
        mock_response.json.return_value = json_data
        if raise_for_status:
            mock_response.raise_for_status.side_effect = raise_for_status
        else:
            mock_response.raise_for_status = MagicMock()
        return mock_response

    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_basic(self, mock_get):
        """Verify basic version fetching and grouping by minor version."""
        mock_settings = self._create_mock_settings()
        mock_get.return_value = self._create_mock_response({
            '4-stable': ['4.18.1', '4.18.2', '4.19.0', '4.20.3']
        })

        result = fetch_ocp_versions(mock_settings)

        expected = {
            '4.18': '4.18.2',
            '4.19': '4.19.0',
            '4.20': '4.20.3',
        }
        self.assertEqual(result, expected)
        mock_get.assert_called_once_with(RELEASE_URL_API, timeout=30)

    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_filters_ignored_minor(self, mock_get):
        """Verify that ignored minor versions are filtered out."""
        mock_settings = self._create_mock_settings(ignored_versions="^4\\.18$")
        mock_get.return_value = self._create_mock_response({
            '4-stable': ['4.18.1', '4.18.2', '4.19.0', '4.20.3']
        })

        result = fetch_ocp_versions(mock_settings)

        expected = {
            '4.19': '4.19.0',
            '4.20': '4.20.3'
        }
        self.assertEqual(result, expected)

    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_filters_exact_version(self, mock_get):
        """Verify that exact version matches are filtered out."""
        mock_settings = self._create_mock_settings(ignored_versions="^4\\.18\\.1$")
        mock_get.return_value = self._create_mock_response({
            '4-stable': ['4.18.1', '4.18.2', '4.19.0']
        })

        result = fetch_ocp_versions(mock_settings)

        expected = {
            '4.18': '4.18.2',
            '4.19': '4.19.0',
        }
        self.assertEqual(result, expected)

    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_selects_highest_patch(self, mock_get):
        """Verify that highest patch version is selected for each minor."""
        mock_settings = self._create_mock_settings()
        mock_get.return_value = self._create_mock_response({
            '4-stable': [
                '4.18.0', '4.18.1', '4.18.2',
                '4.19.5', '4.19.3', '4.19.8', '4.19.4'
            ]
        })

        result = fetch_ocp_versions(mock_settings)

        expected = {
            '4.18': '4.18.2',
            '4.19': '4.19.8',
        }
        self.assertEqual(result, expected)

    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_empty_response(self, mock_get):
        """Verify behavior when API returns empty version list."""
        mock_settings = self._create_mock_settings()
        mock_get.return_value = self._create_mock_response({'4-stable': []})

        result = fetch_ocp_versions(mock_settings)

        self.assertEqual(result, {})

    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_api_error(self, mock_get):
        """Verify that API errors are propagated."""
        mock_settings = self._create_mock_settings()
        mock_get.return_value = self._create_mock_response(
            {},
            raise_for_status=RequestException("API error")
        )

        with self.assertRaises(RequestException):
            fetch_ocp_versions(mock_settings)

    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_invalid_response_structure(self, mock_get):
        """Verify that missing 4-stable key raises KeyError."""
        mock_settings = self._create_mock_settings()
        mock_get.return_value = self._create_mock_response({'some-other-key': []})

        with self.assertRaises(KeyError):
            fetch_ocp_versions(mock_settings)

    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_invalid_semver(self, mock_get):
        """Verify that invalid semver format raises ValueError."""
        mock_settings = self._create_mock_settings()
        mock_get.return_value = self._create_mock_response({
            '4-stable': ['4.18.1', 'not-a-semver', '4.19.0']
        })

        with self.assertRaises(ValueError):
            fetch_ocp_versions(mock_settings)

    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_uses_configured_timeout(self, mock_get):
        """Verify that configured timeout is passed to requests."""
        mock_settings = self._create_mock_settings(timeout=60)
        mock_get.return_value = self._create_mock_response({'4-stable': ['4.18.0']})

        fetch_ocp_versions(mock_settings)

        mock_get.assert_called_once_with(RELEASE_URL_API, timeout=60)


if __name__ == '__main__':
    unittest.main()
