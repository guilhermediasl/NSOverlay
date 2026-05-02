"""
LibreLink Up API client.

Authentication and data-retrieval flow
---------------------------------------
1.  POST /llu/auth/login          →  obtain bearer token + account-id
2.  GET  /llu/connections         →  list patient/follower connections; pick a
                                     connection by index or patient-id from config
3.  GET  /llu/connections/{patientId}/graph
                                  →  retrieve the glucose graph for that patient

Region → base URL mapping
--------------------------
Each LibreView region uses its own subdomain.  Pass the short region code
(e.g. ``"eu"``, ``"us"``) and the client resolves the correct base URL.

    ae  → api-ae.libreview.io
    ap  → api-ap.libreview.io
    au  → api-au.libreview.io
    ca  → api-ca.libreview.io
    de  → api-de.libreview.io
    eu  → api-eu.libreview.io
    fr  → api-fr.libreview.io
    jp  → api-jp.libreview.io
    us  → api-us.libreview.io

Data shape (from the LibreLink Up / nightscout-librelink-up reference)
-----------------------------------------------------------------------
The /graph endpoint returns JSON with the shape::

    {
      "status": 0,
      "data": {
        "connection": {
          "glucoseMeasurement": { GlucoseItem }
        },
        "graphData": [ GlucoseItem, … ]
      }
    }

A ``GlucoseItem`` contains (among other fields)::

    {
      "FactoryTimestamp": "1/1/2024 12:00:00 AM",   # UTC, M/D/YYYY h:mm:ss AM/PM
      "Timestamp":        "1/1/2024 2:00:00 PM",    # local time (server-side)
      "ValueInMgPerDl":   120,
      "TrendArrow":       3                          # optional
    }
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger("nsoverlay")

# ── Region map ───────────────────────────────────────────────────────────────

REGION_URLS: dict[str, str] = {
    "ae": "api-ae.libreview.io",
    "ap": "api-ap.libreview.io",
    "au": "api-au.libreview.io",
    "ca": "api-ca.libreview.io",
    "de": "api-de.libreview.io",
    "eu": "api-eu.libreview.io",
    "fr": "api-fr.libreview.io",
    "jp": "api-jp.libreview.io",
    "us": "api-us.libreview.io",
}

# HTTP headers required by the LibreLink Up mobile app.  The version/product
# values mirror those used by the official iOS/Android apps and must be present
# or the server returns 400.
_LLU_APP_VERSION = "4.7.0"
_LLU_PRODUCT = "llu.ios"

_FACTORY_TS_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",   # e.g.  1/2/2024 3:04:05 PM
    "%m/%d/%Y %H:%M",         # fallback
    "%Y-%m-%dT%H:%M:%S",      # ISO-8601 fallback
)


def _parse_factory_timestamp(raw: str) -> datetime | None:
    """Return a UTC :class:`datetime` from a LibreLink Up ``FactoryTimestamp`` string."""
    for fmt in _FACTORY_TS_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    log.warning("llu_client: cannot parse FactoryTimestamp %r", raw)
    return None


class LibreLinkUpError(Exception):
    """Raised for all LibreLink Up API errors."""


class LibreLinkUpAuthError(LibreLinkUpError):
    """Raised when authentication fails (wrong credentials, expired token, …)."""


class LibreLinkUpRegionError(LibreLinkUpError):
    """Raised when the configured region code is not recognised."""


class LibreLinkUpClient:
    """Minimal LibreLink Up REST client.

    Parameters
    ----------
    email:
        LibreLink Up / LibreView account e-mail address.
    password:
        Account password.
    region:
        Short region code (``"eu"``, ``"us"``, …).  Must be one of the keys in
        :data:`REGION_URLS`.
    patient_index:
        Zero-based index into the connections list that selects which patient to
        follow when there are multiple connections.  Ignored when
        ``patient_id`` is provided.
    patient_id:
        Explicit patient UUID.  Takes priority over *patient_index*.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        email: str,
        password: str,
        region: str = "eu",
        *,
        patient_index: int = 0,
        patient_id: str | None = None,
        timeout: int = 15,
    ) -> None:
        region = region.lower().strip()
        if region not in REGION_URLS:
            raise LibreLinkUpRegionError(
                f"Unknown region {region!r}. "
                f"Valid values: {', '.join(sorted(REGION_URLS))}."
            )

        self._email = email
        self._password = password
        self._base_url = f"https://{REGION_URLS[region]}"
        self._patient_index = patient_index
        self._patient_id = patient_id
        self._timeout = timeout

        self._token: str | None = None
        self._account_id_hash: str | None = None
        self._session = requests.Session()

    # ── Public API ───────────────────────────────────────────────────────────

    def login(self) -> None:
        """Authenticate and store the bearer token.

        Raises
        ------
        LibreLinkUpAuthError
            When the server rejects the credentials or returns a redirect
            asking to re-authenticate against a different regional endpoint.
        LibreLinkUpError
            For any other non-2xx response.
        requests.RequestException
            For network/transport errors.
        """
        url = f"{self._base_url}/llu/auth/login"
        payload = {"email": self._email, "password": self._password}

        log.debug("llu_client: POST %s", url)
        resp = self._session.post(
            url, json=payload, headers=self._base_headers(), timeout=self._timeout
        )

        if resp.status_code == 200:
            body: dict[str, Any] = resp.json()
            status = body.get("status", -1)

            # status 2 → server wants the user to redirect to a different region
            if status == 2:
                redirect_region = (
                    body.get("data", {})
                    .get("redirect", {})
                    .get("region", "unknown")
                )
                raise LibreLinkUpAuthError(
                    f"LibreLink Up redirected to region {redirect_region!r}. "
                    f"Update the 'region' setting in llu_config.json."
                )

            if status != 0:
                raise LibreLinkUpAuthError(
                    f"Login rejected (status={status}). Check your credentials."
                )

            data = body.get("data", {})
            ticket: dict[str, Any] = data.get("authTicket", {})
            self._token = ticket.get("token")
            if not self._token:
                raise LibreLinkUpAuthError(
                    "Login response did not contain an auth token."
                )

            # The account-id is hashed (SHA-256) and sent in every subsequent
            # request header, matching the behaviour of the reference TS code.
            account_id: str = data.get("user", {}).get("id", "")
            self._account_id_hash = (
                hashlib.sha256(account_id.encode()).hexdigest() if account_id else ""
            )
            log.debug("llu_client: login successful, token acquired")

        elif resp.status_code in (401, 403):
            raise LibreLinkUpAuthError(
                "Authentication failed — wrong e-mail or password."
            )
        else:
            raise LibreLinkUpError(
                f"Unexpected login response HTTP {resp.status_code}: {resp.text[:200]}"
            )

    def get_connections(self) -> list[dict[str, Any]]:
        """Return the raw list of patient connection objects.

        Raises
        ------
        LibreLinkUpAuthError
            If the token is missing or rejected.
        LibreLinkUpError
            For non-2xx responses.
        requests.RequestException
            For network/transport errors.
        """
        self._require_auth()
        url = f"{self._base_url}/llu/connections"
        log.debug("llu_client: GET %s", url)
        resp = self._session.get(
            url, headers=self._auth_headers(), timeout=self._timeout
        )
        self._check_response(resp)
        body: dict[str, Any] = resp.json()
        connections: list[dict[str, Any]] = body.get("data", []) or []
        log.debug("llu_client: %d connection(s) returned", len(connections))
        return connections

    def get_glucose_graph(self, patient_id: str | None = None) -> dict[str, Any]:
        """Return the raw ``data`` dict from the /graph endpoint.

        If *patient_id* is not provided the client uses the ``patient_id`` or
        ``patient_index`` supplied at construction time.

        Returns
        -------
        dict
            The ``data`` field of the graph response, with keys
            ``connection`` and ``graphData``.

        Raises
        ------
        LibreLinkUpError
            If no connections are found or the patient cannot be resolved.
        requests.RequestException
            For network/transport errors.
        """
        self._require_auth()

        pid = patient_id or self._patient_id
        if not pid:
            pid = self._resolve_patient_id()

        url = f"{self._base_url}/llu/connections/{pid}/graph"
        log.debug("llu_client: GET /llu/connections/<patientId>/graph")
        resp = self._session.get(
            url, headers=self._auth_headers(), timeout=self._timeout
        )
        self._check_response(resp)
        body: dict[str, Any] = resp.json()
        return body.get("data", {})

    def fetch_glucose_entries(
        self,
        *,
        entries_to_fetch: int = 36,
        time_window_hours: int = 3,
    ) -> list[dict[str, Any]]:
        """High-level helper: return a filtered list of glucose entries.

        Filtering logic
        ---------------
        1.  Call ``get_glucose_graph()`` to retrieve the full ``graphData``
            array plus the current ``connection.glucoseMeasurement``.
        2.  Combine ``graphData`` entries with the current measurement (if it
            is not already in the array) to build a complete timeline.
        3.  Keep only entries whose ``FactoryTimestamp`` (UTC) falls within the
            last *time_window_hours* hours.
        4.  Sort by timestamp and take the **most-recent** *entries_to_fetch*
            entries.

        Each returned dict is guaranteed to contain at minimum:
        ``FactoryTimestamp``, ``ValueInMgPerDl``, and ``_ts`` (a float Unix
        timestamp derived from ``FactoryTimestamp``, for easy graphing).

        Parameters
        ----------
        entries_to_fetch:
            Maximum number of entries to return (``X`` in the problem spec).
        time_window_hours:
            Only include entries newer than ``now - time_window_hours``.
            Must be 1, 2, or 3.
        """
        if time_window_hours not in (1, 2, 3):
            raise ValueError("time_window_hours must be 1, 2, or 3.")

        graph_data = self.get_glucose_graph()

        raw_entries: list[dict[str, Any]] = list(graph_data.get("graphData") or [])

        # Include the current (live) reading from connection.glucoseMeasurement
        current_measurement: dict[str, Any] | None = (
            graph_data.get("connection", {}).get("glucoseMeasurement")
        )
        if current_measurement and isinstance(current_measurement, dict):
            current_ts = current_measurement.get("FactoryTimestamp")
            existing_ts = {e.get("FactoryTimestamp") for e in raw_entries}
            if current_ts and current_ts not in existing_ts:
                raw_entries.append(current_measurement)

        if not raw_entries:
            log.warning("llu_client: no glucose entries returned from API")
            return []

        # Parse timestamps and attach a float Unix timestamp for easy sorting
        cutoff = datetime.now(tz=timezone.utc).timestamp() - time_window_hours * 3600
        parsed: list[dict[str, Any]] = []
        for entry in raw_entries:
            raw_ts = entry.get("FactoryTimestamp", "")
            dt = _parse_factory_timestamp(raw_ts) if raw_ts else None
            if dt is None:
                continue
            ts = dt.timestamp()
            entry_copy = dict(entry)   # shallow copy so callers get clean dicts
            entry_copy["_ts"] = ts
            if ts >= cutoff:
                parsed.append(entry_copy)

        # Sort ascending, then take the most-recent X
        parsed.sort(key=lambda e: e["_ts"])
        result = parsed[-entries_to_fetch:]
        log.debug(
            "llu_client: %d entries in window, returning %d",
            len(parsed),
            len(result),
        )
        return result

    # ── Private helpers ──────────────────────────────────────────────────────

    def _base_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "version": _LLU_APP_VERSION,
            "product": _LLU_PRODUCT,
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

    def _auth_headers(self) -> dict[str, str]:
        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self._token}"
        if self._account_id_hash:
            headers["Account-Id"] = self._account_id_hash
        return headers

    def _require_auth(self) -> None:
        if not self._token:
            raise LibreLinkUpAuthError(
                "Not authenticated. Call login() first."
            )

    def _check_response(self, resp: requests.Response) -> None:
        if resp.status_code in (401, 403):
            self._token = None  # force re-login on next attempt
            raise LibreLinkUpAuthError(
                f"Session expired or access denied (HTTP {resp.status_code}). "
                "Call login() again."
            )
        if not resp.ok:
            raise LibreLinkUpError(
                f"API request failed with HTTP {resp.status_code}: {resp.text[:200]}"
            )

    def _resolve_patient_id(self) -> str:
        """Pick a patient id from the connections list using the configured index."""
        connections = self.get_connections()
        if not connections:
            raise LibreLinkUpError(
                "No LibreLink Up connections found for this account. "
                "Make sure a follower connection has been set up in the LibreLink Up app."
            )
        index = self._patient_index
        if index >= len(connections):
            raise LibreLinkUpError(
                f"patient_index {index} is out of range "
                f"(account has {len(connections)} connection(s))."
            )
        pid: str = connections[index].get("patientId", "")
        if not pid:
            raise LibreLinkUpError(
                f"Connection at index {index} has no patientId field."
            )
        log.debug("llu_client: using patientId at index %d", index)
        return pid
