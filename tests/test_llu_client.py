"""
Unit tests for src/data/llu_client.py.

These tests use unittest.mock to avoid real network calls so they run
offline without any LibreLink Up credentials.
"""

from __future__ import annotations

import importlib.util
import sys
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Import llu_client directly (bypassing the package __init__ which pulls in
# PyQt6 via remote_fetch_thread, so these tests stay lightweight).
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
_MODULE_PATH = os.path.join(_REPO_ROOT, "src", "data", "llu_client.py")

spec = importlib.util.spec_from_file_location("llu_client", _MODULE_PATH)
_llu_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(_llu_mod)  # type: ignore[union-attr]

LibreLinkUpAuthError = _llu_mod.LibreLinkUpAuthError
LibreLinkUpClient = _llu_mod.LibreLinkUpClient
LibreLinkUpError = _llu_mod.LibreLinkUpError
LibreLinkUpRegionError = _llu_mod.LibreLinkUpRegionError
REGION_URLS = _llu_mod.REGION_URLS
_parse_factory_timestamp = _llu_mod._parse_factory_timestamp


# ---------------------------------------------------------------------------
# Helper to create a mock response object
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

class TestParseFactoryTimestamp(unittest.TestCase):
    def test_standard_format(self):
        dt = _parse_factory_timestamp("1/2/2024 3:04:05 PM")
        self.assertIsNotNone(dt)
        self.assertIsInstance(dt, datetime)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 2)
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.hour, 15)
        self.assertEqual(dt.minute, 4)

    def test_am_format(self):
        dt = _parse_factory_timestamp("6/15/2023 8:30:00 AM")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 8)

    def test_invalid_returns_none(self):
        dt = _parse_factory_timestamp("not-a-date")
        self.assertIsNone(dt)

    def test_empty_string_returns_none(self):
        dt = _parse_factory_timestamp("")
        self.assertIsNone(dt)


# ---------------------------------------------------------------------------
# Region validation
# ---------------------------------------------------------------------------

class TestRegionValidation(unittest.TestCase):
    def test_valid_regions_accepted(self):
        for region in REGION_URLS:
            # Should not raise
            client = LibreLinkUpClient("a@b.com", "pw", region=region)
            self.assertIsNotNone(client)

    def test_invalid_region_raises(self):
        with self.assertRaises(LibreLinkUpRegionError):
            LibreLinkUpClient("a@b.com", "pw", region="xx")

    def test_region_is_case_insensitive(self):
        client = LibreLinkUpClient("a@b.com", "pw", region="EU")
        self.assertIsNotNone(client)
        self.assertIn("api-eu.libreview.io", client._base_url)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class TestLogin(unittest.TestCase):
    def _make_client(self) -> LibreLinkUpClient:
        return LibreLinkUpClient("test@example.com", "secret", region="eu")

    def test_successful_login_stores_token(self):
        client = self._make_client()
        mock_resp = _mock_response(200, {
            "status": 0,
            "data": {
                "authTicket": {"token": "tok123", "expires": 9999999999},
                "user": {"id": "user-abc"},
            },
        })
        with patch.object(client._session, "post", return_value=mock_resp):
            client.login()
        self.assertEqual(client._token, "tok123")
        self.assertIsNotNone(client._account_id_hash)

    def test_wrong_credentials_raises_auth_error(self):
        client = self._make_client()
        mock_resp = _mock_response(401, {})
        with patch.object(client._session, "post", return_value=mock_resp):
            with self.assertRaises(LibreLinkUpAuthError):
                client.login()

    def test_wrong_region_redirect_raises_auth_error(self):
        client = self._make_client()
        mock_resp = _mock_response(200, {
            "status": 2,
            "data": {"redirect": {"region": "de"}},
        })
        with patch.object(client._session, "post", return_value=mock_resp):
            with self.assertRaises(LibreLinkUpAuthError) as ctx:
                client.login()
        self.assertIn("de", str(ctx.exception))

    def test_unexpected_http_error_raises(self):
        client = self._make_client()
        mock_resp = _mock_response(500, {})
        with patch.object(client._session, "post", return_value=mock_resp):
            with self.assertRaises(LibreLinkUpError):
                client.login()


# ---------------------------------------------------------------------------
# Authenticated calls require a token
# ---------------------------------------------------------------------------

class TestRequireAuth(unittest.TestCase):
    def test_get_connections_without_login_raises(self):
        client = LibreLinkUpClient("a@b.com", "pw", region="eu")
        with self.assertRaises(LibreLinkUpAuthError):
            client.get_connections()

    def test_get_glucose_graph_without_login_raises(self):
        client = LibreLinkUpClient("a@b.com", "pw", region="eu")
        with self.assertRaises(LibreLinkUpAuthError):
            client.get_glucose_graph(patient_id="some-id")


# ---------------------------------------------------------------------------
# fetch_glucose_entries filtering
# ---------------------------------------------------------------------------

def _make_authenticated_client() -> LibreLinkUpClient:
    client = LibreLinkUpClient("a@b.com", "pw", region="eu")
    client._token = "fake-token"
    client._account_id_hash = "fakehash"
    return client


def _entry(minutes_ago: float, value: int = 100) -> dict:
    """Create a fake GlucoseItem with FactoryTimestamp set *minutes_ago* minutes back."""
    dt = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "FactoryTimestamp": dt.strftime("%m/%d/%Y %I:%M:%S %p"),
        "ValueInMgPerDl": value,
    }


class TestFetchGlucoseEntries(unittest.TestCase):
    def test_entries_filtered_by_time_window(self):
        client = _make_authenticated_client()
        # 5 entries within 1 hour, 3 entries older than 1 hour
        graph_data = {
            "graphData": [
                _entry(200, 90),   # 3 h 20 m ago — outside 1-h window
                _entry(70, 100),   # just outside 1-h window
                _entry(55, 110),
                _entry(40, 120),
                _entry(25, 130),
                _entry(10, 140),
                _entry(5, 150),
            ],
            "connection": {"glucoseMeasurement": None},
        }

        with patch.object(client, "get_glucose_graph", return_value=graph_data):
            result = client.fetch_glucose_entries(
                entries_to_fetch=100, time_window_hours=1
            )

        # Entries from 55 min ago and newer should be included.
        # time_window_tolerance_seconds accounts for the small delta between the
        # timestamp stored in the entry and the cutoff computed during the test.
        time_window_tolerance_seconds = 5
        self.assertTrue(all(
            e["_ts"] >= (datetime.now(tz=timezone.utc) - timedelta(hours=1)).timestamp()
            - time_window_tolerance_seconds
            for e in result
        ))
        self.assertLessEqual(len(result), 5)

    def test_entries_capped_by_x(self):
        client = _make_authenticated_client()
        graph_data = {
            "graphData": [_entry(i * 5, 100 + i) for i in range(20)],
            "connection": {"glucoseMeasurement": None},
        }

        with patch.object(client, "get_glucose_graph", return_value=graph_data):
            result = client.fetch_glucose_entries(
                entries_to_fetch=5, time_window_hours=3
            )

        self.assertEqual(len(result), 5)

    def test_empty_graph_data_returns_empty_list(self):
        client = _make_authenticated_client()
        graph_data = {
            "graphData": [],
            "connection": {"glucoseMeasurement": None},
        }

        with patch.object(client, "get_glucose_graph", return_value=graph_data):
            result = client.fetch_glucose_entries(
                entries_to_fetch=10, time_window_hours=1
            )

        self.assertEqual(result, [])

    def test_current_measurement_included_if_not_duplicate(self):
        client = _make_authenticated_client()
        current = _entry(1, 180)  # 1 minute ago
        graph_data = {
            "graphData": [_entry(10, 160), _entry(5, 170)],
            "connection": {"glucoseMeasurement": current},
        }

        with patch.object(client, "get_glucose_graph", return_value=graph_data):
            result = client.fetch_glucose_entries(
                entries_to_fetch=10, time_window_hours=1
            )

        values = [e["ValueInMgPerDl"] for e in result]
        self.assertIn(180, values)

    def test_invalid_time_window_raises(self):
        client = _make_authenticated_client()
        with self.assertRaises(ValueError):
            client.fetch_glucose_entries(time_window_hours=4)

    def test_results_sorted_ascending(self):
        client = _make_authenticated_client()
        # Entries in reverse order
        graph_data = {
            "graphData": [_entry(i * 5) for i in range(10, 0, -1)],
            "connection": {"glucoseMeasurement": None},
        }

        with patch.object(client, "get_glucose_graph", return_value=graph_data):
            result = client.fetch_glucose_entries(
                entries_to_fetch=100, time_window_hours=3
            )

        timestamps = [e["_ts"] for e in result]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_ts_field_attached_to_each_entry(self):
        client = _make_authenticated_client()
        graph_data = {
            "graphData": [_entry(5, 110)],
            "connection": {"glucoseMeasurement": None},
        }

        with patch.object(client, "get_glucose_graph", return_value=graph_data):
            result = client.fetch_glucose_entries(
                entries_to_fetch=5, time_window_hours=1
            )

        self.assertIn("_ts", result[0])
        self.assertIsInstance(result[0]["_ts"], float)

    def test_original_entry_not_mutated(self):
        """fetch_glucose_entries must not mutate the original entry dicts."""
        client = _make_authenticated_client()
        original = _entry(5, 110)
        graph_data = {
            "graphData": [original],
            "connection": {"glucoseMeasurement": None},
        }

        with patch.object(client, "get_glucose_graph", return_value=graph_data):
            client.fetch_glucose_entries(entries_to_fetch=5, time_window_hours=1)

        self.assertNotIn("_ts", original)


# ---------------------------------------------------------------------------
# Patient-id resolution
# ---------------------------------------------------------------------------

class TestResolvePatientId(unittest.TestCase):
    def test_uses_explicit_patient_id(self):
        client = _make_authenticated_client()
        client._patient_id = "explicit-pid"

        mock_graph = {"graphData": [], "connection": {}}
        resp = _mock_response(200, {"data": mock_graph})
        with patch.object(client._session, "get", return_value=resp) as mock_get:
            client.get_glucose_graph()
        url_called = mock_get.call_args[0][0]
        self.assertIn("explicit-pid", url_called)

    def test_resolves_by_index(self):
        client = _make_authenticated_client()
        client._patient_index = 1

        connections_resp = _mock_response(200, {
            "data": [
                {"patientId": "pid-0"},
                {"patientId": "pid-1"},
            ]
        })
        graph_resp = _mock_response(200, {"data": {"graphData": [], "connection": {}}})

        with patch.object(client._session, "get", side_effect=[connections_resp, graph_resp]) as mock_get:
            client.get_glucose_graph()
        graph_url = mock_get.call_args_list[1][0][0]
        self.assertIn("pid-1", graph_url)

    def test_no_connections_raises(self):
        client = _make_authenticated_client()
        resp = _mock_response(200, {"data": []})
        with patch.object(client._session, "get", return_value=resp):
            with self.assertRaises(LibreLinkUpError):
                client.get_glucose_graph()

    def test_index_out_of_range_raises(self):
        client = _make_authenticated_client()
        client._patient_index = 5
        resp = _mock_response(200, {"data": [{"patientId": "pid-0"}]})
        with patch.object(client._session, "get", return_value=resp):
            with self.assertRaises(LibreLinkUpError):
                client.get_glucose_graph()


if __name__ == "__main__":
    unittest.main()
