#!/usr/bin/env python

import unittest
from unittest.mock import patch, MagicMock
from requests.exceptions import RequestException

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from workflows.gpu_operator_versions.openshift import fetch_ocp_versions, RELEASE_URL_API


class TestOpenShift(unittest.TestCase):
    """Test cases for AMD CI openshift.py functions."""

    @patch('workflows.gpu_operator_versions.openshift.Settings')
    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_basic(self, mock_get, mock_settings):
        """Test basic functionality of fetch_ocp_versions."""
        mock_settings.ignored_versions = "x^"
        mock_settings.request_timeout_sec = 30

        mock_response = MagicMock()
        mock_response.json.return_value = {
            '4-stable': ['4.18.1', '4.18.2', '4.19.0', '4.20.3']}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_ocp_versions(mock_settings)

        expected = {
            '4.18': '4.18.2',
            '4.19': '4.19.0',
            '4.20': '4.20.3',
        }
        self.assertEqual(result, expected)
        mock_get.assert_called_once_with(RELEASE_URL_API, timeout=30)
        mock_response.raise_for_status.assert_called_once()

    @patch('workflows.gpu_operator_versions.openshift.Settings')
    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_ignored(self, mock_get, mock_settings):
        """Test that ignored versions are correctly filtered out."""
        mock_settings.ignored_versions = "^4.18$|^4.20.0-rc.1$"
        mock_settings.request_timeout_sec = 30

        mock_response = MagicMock()
        mock_response.json.return_value = {
            '4-stable': ['4.18.1', '4.18.2', '4.19.0', '4.20.3', '4.20.0-rc.0', '4.20.0-rc.1']}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_ocp_versions(mock_settings)

        expected = {
            '4.19': '4.19.0',
            '4.20': '4.20.3'
        }
        self.assertEqual(result, expected)

    @patch('workflows.gpu_operator_versions.openshift.Settings')
    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_highest_patch(self, mock_get, mock_settings):
        """Test that highest patch version is selected for each minor version."""
        mock_settings.ignored_versions = "x^"
        mock_settings.request_timeout_sec = 30

        mock_response = MagicMock()
        mock_response.json.return_value = {'4-stable': [
            '4.18.0', '4.18.1', '4.18.2', '4.18.1-rc.3',
            '4.19.5', '4.19.3', '4.19.8', '4.19.4'
        ]}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_ocp_versions(mock_settings)

        expected = {
            '4.18': '4.18.2',
            '4.19': '4.19.8',
        }
        self.assertEqual(result, expected)

    @patch('workflows.gpu_operator_versions.openshift.Settings')
    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_empty_response(self, mock_get, mock_settings):
        """Test behavior when API returns an empty list of versions."""
        mock_settings.ignored_versions = "x^"
        mock_settings.request_timeout_sec = 30

        # Mock empty API response
        mock_response = MagicMock()
        mock_response.json.return_value = {'4-stable': []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # Call the function
        result = fetch_ocp_versions(mock_settings)

        # Verify the result is an empty dictionary
        self.assertEqual(result, {})

    @patch('workflows.gpu_operator_versions.openshift.Settings')
    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_api_error(self, mock_get, mock_settings):
        """Test error handling when API request fails."""
        mock_settings.ignored_versions = "x^"
        mock_settings.request_timeout_sec = 30

        # Mock API error
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = RequestException(
            "API error")
        mock_get.return_value = mock_response

        # Verify the exception is raised
        with self.assertRaises(RequestException):
            fetch_ocp_versions(mock_settings)

    @patch('workflows.gpu_operator_versions.openshift.Settings')
    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_invalid_response(self, mock_get, mock_settings):
        """Test behavior when API returns an invalid response structure."""
        mock_settings.ignored_versions = "x^"
        mock_settings.request_timeout_sec = 30

        # Mock invalid API response (missing 4-stable key)
        mock_response = MagicMock()
        mock_response.json.return_value = {'some-other-key': []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # Verify the exception is raised
        with self.assertRaises(KeyError):
            fetch_ocp_versions(mock_settings)

    @patch('workflows.gpu_operator_versions.openshift.Settings')
    @patch('workflows.gpu_operator_versions.openshift.requests.get')
    def test_fetch_ocp_versions_invalid_semver(self, mock_get, mock_settings):
        """Test behavior when API returns invalid semver format."""
        mock_settings.ignored_versions = "x^"
        mock_settings.request_timeout_sec = 30

        # Mock API response with invalid semver
        mock_response = MagicMock()
        mock_response.json.return_value = {
            '4-stable': ['4.18.1', 'not-a-semver', '4.19.0']}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # Call the function - it should skip the invalid version
        with self.assertRaises(ValueError):
            fetch_ocp_versions(mock_settings)


if __name__ == '__main__':
    unittest.main()

