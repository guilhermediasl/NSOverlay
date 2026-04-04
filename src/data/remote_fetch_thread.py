from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Mapping

import requests
from PyQt6.QtCore import QThread, pyqtSignal


JsonDict = dict[str, Any]


@dataclass
class FetchPayload:
    api_secret: str
    nightscout_url: str
    entries_cache: list[JsonDict]
    entries_to_fetch: int
    fetch_remote: bool
    treatments_cache: list[JsonDict]
    fetch_treatments: bool
    treatments_to_fetch: int

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FetchPayload":
        return cls(
            api_secret=str(payload.get("api_secret", "")),
            nightscout_url=str(payload.get("nightscout_url", "")),
            entries_cache=list(payload.get("entries_cache", [])),
            entries_to_fetch=int(payload.get("entries_to_fetch", 90)),
            fetch_remote=bool(payload.get("fetch_remote", True)),
            treatments_cache=list(payload.get("treatments_cache", [])),
            fetch_treatments=bool(payload.get("fetch_treatments", False)),
            treatments_to_fetch=int(payload.get("treatments_to_fetch", 50)),
        )


@dataclass
class FetchResult:
    entries_cache: list[JsonDict]
    treatments_cache: list[JsonDict]

    def to_signal_payload(self) -> JsonDict:
        return {
            "entries_cache": self.entries_cache,
            "treatments_cache": self.treatments_cache,
        }


class RemoteFetchThread(QThread):
    """Persistent background worker thread for Nightscout fetches."""

    resultReady = pyqtSignal(dict)
    fetchError = pyqtSignal(str)

    def __init__(self, payload: Mapping[str, Any] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._lock = threading.Condition()
        self._latest_payload: FetchPayload | None = (
            FetchPayload.from_mapping(payload) if payload is not None else None
        )
        self._stop = False
        self._session = requests.Session()

    def submit(self, payload: Mapping[str, Any] | FetchPayload) -> None:
        with self._lock:
            self._latest_payload = (
                payload if isinstance(payload, FetchPayload) else FetchPayload.from_mapping(payload)
            )
            self._lock.notify()

    def stop(self) -> None:
        with self._lock:
            self._stop = True
            self._lock.notify()

    def run(self) -> None:
        while True:
            with self._lock:
                while not self._stop and self._latest_payload is None:
                    self._lock.wait()

                if self._stop:
                    self._close_session()
                    return

                payload = self._latest_payload
                self._latest_payload = None

            if payload is None:
                continue

            try:
                result = self._fetch_once(payload)
                self.resultReady.emit(result.to_signal_payload())
            except Exception as e:
                self.fetchError.emit(str(e))

    def _close_session(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def _fetch_once(self, payload: FetchPayload) -> FetchResult:
        headers = {"api-secret": payload.api_secret}
        base_url = payload.nightscout_url

        entries_cache = list(payload.entries_cache)
        entries_to_fetch = max(1, min(payload.entries_to_fetch, 288))
        fetch_remote = payload.fetch_remote

        if fetch_remote or not entries_cache:
            last_date_ms = entries_cache[-1].get("date") if entries_cache else None
            if last_date_ms:
                url = (
                    f"{base_url}/api/v1/entries.json"
                    f"?find[date][$gt]={last_date_ms}&count=5"
                )
            else:
                url = f"{base_url}/api/v1/entries.json?count={entries_to_fetch}"

            response = self._session.get(url, headers=headers, timeout=5)
            response.raise_for_status()
            new_entries = response.json()
            if not isinstance(new_entries, list):
                raise ValueError(
                    f"Unexpected entries response type: {type(new_entries).__name__}"
                )

            if new_entries:
                existing_ids = {e.get("_id") for e in entries_cache}
                for entry in new_entries:
                    if isinstance(entry, dict) and entry.get("_id") not in existing_ids:
                        entries_cache.append(entry)
                        existing_ids.add(entry.get("_id"))
                entries_cache.sort(key=lambda e: e.get("date", 0))
                if len(entries_cache) > entries_to_fetch:
                    entries_cache = entries_cache[-entries_to_fetch:]

        treatments_cache = list(payload.treatments_cache)
        fetch_treatments = payload.fetch_treatments
        treatments_to_fetch = payload.treatments_to_fetch
        treatments_to_fetch = max(1, min(treatments_to_fetch, 500))

        if fetch_treatments:
            if not treatments_cache:
                t_url = f"{base_url}/api/v1/treatments.json?count={treatments_to_fetch}"
                t_resp = self._session.get(t_url, headers=headers, timeout=5)
                t_resp.raise_for_status()
                new_treatments = t_resp.json()
                if not isinstance(new_treatments, list):
                    raise ValueError(
                        "Unexpected treatments response type: "
                        f"{type(new_treatments).__name__}"
                    )
            else:
                probe_url = f"{base_url}/api/v1/treatments.json?count=1"
                probe_resp = self._session.get(probe_url, headers=headers, timeout=5)
                probe_resp.raise_for_status()
                probe = probe_resp.json()
                if not isinstance(probe, list):
                    raise ValueError(
                        f"Unexpected treatments response type: {type(probe).__name__}"
                    )

                probe_id = probe[0].get("_id") if probe else None
                cached_id = treatments_cache[-1].get("_id")

                if not probe or probe_id == cached_id:
                    new_treatments = []
                else:
                    t_url = f"{base_url}/api/v1/treatments.json?count=5"
                    t_resp = self._session.get(t_url, headers=headers, timeout=5)
                    t_resp.raise_for_status()
                    new_treatments = t_resp.json()
                    if not isinstance(new_treatments, list):
                        raise ValueError(
                            "Unexpected treatments response type: "
                            f"{type(new_treatments).__name__}"
                        )

            if new_treatments:
                existing_t_ids = {t.get("_id") for t in treatments_cache}
                for treatment in new_treatments:
                    if isinstance(treatment, dict) and treatment.get("_id") not in existing_t_ids:
                        treatments_cache.append(treatment)
                        existing_t_ids.add(treatment.get("_id"))
                treatments_cache.sort(key=lambda t: t.get("created_at", ""))
                if len(treatments_cache) > treatments_to_fetch:
                    treatments_cache = treatments_cache[-treatments_to_fetch:]

        return FetchResult(entries_cache=entries_cache, treatments_cache=treatments_cache)
