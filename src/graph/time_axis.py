from __future__ import annotations

from datetime import datetime
from typing import Sequence

import pyqtgraph as pg


class TimeAxisItem(pg.AxisItem):
    """Custom axis item to display time in 24-hour format."""

    def tickStrings(
        self, values: Sequence[float], scale: float, spacing: float
    ) -> list[str]:
        strings: list[str] = []
        for value in values:
            try:
                dt = datetime.fromtimestamp(value)
                strings.append(dt.strftime("%H:%M"))
            except (ValueError, OSError):
                strings.append("")
        return strings
