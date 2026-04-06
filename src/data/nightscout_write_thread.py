from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from PyQt6.QtCore import QThread, pyqtSignal


JsonDict = dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class TreatmentWriteRequest:
    nightscout_url: str
    api_secret: str
    event_type: str
    insulin: float = 0.0
    insulin_type: str = "Humalog Lispro"
    carbs: int = 0
    notes: str = ""
    entered_by: str = "NSOverlay"
    created_at: str = ""

    def to_payload(self) -> JsonDict:
        payload: JsonDict = {
            "eventType": self.event_type.strip(),
            "created_at": self.created_at.strip() or _utc_now_iso(),
            "enteredBy": self.entered_by.strip() or "NSOverlay",
        }
        if self.insulin > 0:
            payload["insulin"] = round(float(self.insulin), 2)
            insulin_type = self.insulin_type.strip()
            if insulin_type:
                payload["insulinType"] = insulin_type
        if self.carbs > 0:
            payload["carbs"] = int(self.carbs)
        notes = self.notes.strip()
        if notes:
            payload["notes"] = notes
        return payload


class NightscoutTreatmentWriteThread(QThread):
    submitted = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, request: TreatmentWriteRequest, parent=None) -> None:
        super().__init__(parent)
        self._request = request

    def run(self) -> None:
        try:
            url = f"{self._request.nightscout_url.rstrip('/')}/api/v1/treatments/"
            response = requests.post(
                url,
                json=self._request.to_payload(),
                headers={"api-secret": self._request.api_secret},
                timeout=10,
            )
            response.raise_for_status()

            if not response.content:
                result: JsonDict = {}
            else:
                try:
                    parsed = response.json()
                except ValueError:
                    parsed = {"response": response.text.strip()}

                if isinstance(parsed, dict):
                    result = parsed
                else:
                    result = {"response": parsed}

            self.submitted.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))