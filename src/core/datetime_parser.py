from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


class DateTimeParser:
    """Centralized Nightscout datetime parsing with bounded cache."""

    def __init__(self, max_cache_size: int = 4096) -> None:
        self._cache: dict[str, Optional[datetime]] = {}
        self._max_cache_size = max_cache_size

    def parse(self, raw: str) -> Optional[datetime]:
        if raw in self._cache:
            return self._cache[raw]

        result = self._parse_iso(raw) or self._parse_formats(raw)
        self._add_to_cache(raw, result)
        return result

    def _parse_iso(self, raw: str) -> Optional[datetime]:
        iso_value = raw
        if iso_value.endswith("Z"):
            iso_value = iso_value[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(iso_value)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            return None

    def _parse_formats(self, raw: str) -> Optional[datetime]:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    def _add_to_cache(self, key: str, value: Optional[datetime]) -> None:
        self._cache[key] = value
        if len(self._cache) > self._max_cache_size:
            self._cache.pop(next(iter(self._cache)))
