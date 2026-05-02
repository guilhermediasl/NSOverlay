"""
llu_glucose_display.py — LibreLink Up Glucose Graph Display
============================================================

A standalone desktop widget that retrieves glucose data directly from the
LibreLink Up API and renders it as an interactive graph.

Configuration
-------------
Copy ``llu_config.json.example`` to ``llu_config.json`` and fill in your
credentials:

    {
        "email":             "you@example.com",
        "password":          "your_password",
        "region":            "eu",
        "patient_index":     0,
        "patient_id":        "",
        "entries_to_fetch":  36,
        "time_window_hours": 3,
        "target_low":        70,
        "target_high":       180,
        "refresh_interval_ms": 60000,
        "widget_width":      520,
        "widget_height":     320
    }

Filtering logic
---------------
1.  The client calls GET /llu/connections/{patientId}/graph, which returns
    ``graphData`` (historical readings) plus ``connection.glucoseMeasurement``
    (the latest live reading).
2.  Both sources are merged; duplicate timestamps are deduplicated.
3.  Only entries whose ``FactoryTimestamp`` (UTC) falls within the last
    ``time_window_hours`` hours are kept.
4.  The remaining entries are sorted ascending and the most-recent
    ``entries_to_fetch`` are selected.
So: the graph shows at most X points and never shows points older than the
selected time window — whichever constraint is more restrictive wins.

Usage
-----
    python llu_glucose_display.py

Dependencies
------------
    pip install PyQt6 pyqtgraph requests
(All already listed in requirements.txt.)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Resolve paths relative to the script / frozen executable location
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(_BASE_DIR, "llu_config.json")

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("llu_display")

# ---------------------------------------------------------------------------
# Import the LLU client from the project's src package (when run from the
# repo root).  Fall back to a relative import for frozen builds.
# ---------------------------------------------------------------------------
try:
    from src.data.llu_client import (
        REGION_URLS,
        LibreLinkUpAuthError,
        LibreLinkUpClient,
        LibreLinkUpError,
        LibreLinkUpRegionError,
    )
except ModuleNotFoundError:
    # If running as a frozen executable the src package may not be on sys.path
    sys.path.insert(0, _BASE_DIR)
    from src.data.llu_client import (  # type: ignore[no-redef]
        REGION_URLS,
        LibreLinkUpAuthError,
        LibreLinkUpClient,
        LibreLinkUpError,
        LibreLinkUpRegionError,
    )

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, Any] = {
    "email": "",
    "password": "",
    "region": "eu",
    "patient_index": 0,
    "patient_id": "",
    "entries_to_fetch": 36,
    "time_window_hours": 3,
    "target_low": 70,
    "target_high": 180,
    "refresh_interval_ms": 60000,
    "widget_width": 520,
    "widget_height": 320,
}


# ===========================================================================
# Config helpers
# ===========================================================================

def _load_config() -> dict[str, Any]:
    """Load llu_config.json, falling back to defaults for missing keys."""
    cfg = dict(_DEFAULTS)
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as fh:
                loaded = json.load(fh)
            cfg.update(loaded)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read %s: %s", CONFIG_FILE, exc)
    return cfg


def _save_config(cfg: dict[str, Any]) -> None:
    """Persist config dict to llu_config.json (credentials excluded from log)."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=4)
        log.debug("Config saved to %s", CONFIG_FILE)
    except OSError as exc:
        log.error("Could not save config: %s", exc)


# ===========================================================================
# First-run / edit connection dialog
# ===========================================================================

class _SetupDialog(QDialog):
    """Dialog to collect LibreLink Up credentials and display settings."""

    def __init__(self, cfg: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LibreLink Up — Connection Setup")
        self.setMinimumWidth(400)
        self._cfg = dict(cfg)

        layout = QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 8)
        layout.setSpacing(8)

        self._email = QLineEdit(cfg.get("email", ""))
        self._email.setPlaceholderText("you@example.com")
        layout.addRow("E-mail:", self._email)

        self._password = QLineEdit(cfg.get("password", ""))
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("••••••••")
        layout.addRow("Password:", self._password)

        self._region = QComboBox()
        for code in sorted(REGION_URLS.keys()):
            self._region.addItem(code.upper(), code)
        current_region = cfg.get("region", "eu").lower()
        idx = self._region.findData(current_region)
        if idx >= 0:
            self._region.setCurrentIndex(idx)
        layout.addRow("Region:", self._region)

        self._patient_index = QSpinBox()
        self._patient_index.setRange(0, 9)
        self._patient_index.setValue(int(cfg.get("patient_index", 0)))
        self._patient_index.setToolTip(
            "Zero-based index of the connection to use when there are multiple "
            "patients linked to this account.  Usually 0."
        )
        layout.addRow("Patient index:", self._patient_index)

        self._patient_id = QLineEdit(cfg.get("patient_id", ""))
        self._patient_id.setPlaceholderText("(leave blank to use patient index)")
        layout.addRow("Patient UUID (optional):", self._patient_id)

        self._entries = QSpinBox()
        self._entries.setRange(1, 288)
        self._entries.setValue(int(cfg.get("entries_to_fetch", 36)))
        self._entries.setToolTip("Maximum number of glucose entries to display (X)")
        layout.addRow("Entries to fetch (X):", self._entries)

        self._window = QComboBox()
        for h in (1, 2, 3):
            self._window.addItem(f"{h} hour{'s' if h > 1 else ''}", h)
        wv = int(cfg.get("time_window_hours", 3))
        wi = self._window.findData(wv)
        if wi >= 0:
            self._window.setCurrentIndex(wi)
        self._window.setToolTip("Show only entries within this time window")
        layout.addRow("Time window:", self._window)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_values(self) -> dict[str, Any]:
        cfg = dict(self._cfg)
        cfg["email"] = self._email.text().strip()
        cfg["password"] = self._password.text()
        cfg["region"] = self._region.currentData()
        cfg["patient_index"] = self._patient_index.value()
        cfg["patient_id"] = self._patient_id.text().strip()
        cfg["entries_to_fetch"] = self._entries.value()
        cfg["time_window_hours"] = self._window.currentData()
        return cfg


# ===========================================================================
# Background fetch thread
# ===========================================================================

class _FetchThread(QThread):
    """Background QThread that authenticates and fetches glucose entries."""

    result_ready = pyqtSignal(list)      # emits list[dict]
    fetch_error = pyqtSignal(str)        # emits human-readable error message

    def __init__(self, cfg: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = dict(cfg)
        self._client: LibreLinkUpClient | None = None

    def run(self) -> None:
        try:
            self._ensure_client()
            entries = self._fetch_with_reauth()
            self.result_ready.emit(entries)
        except LibreLinkUpRegionError as exc:
            self.fetch_error.emit(f"Invalid region: {exc}")
        except LibreLinkUpAuthError as exc:
            self._client = None  # force fresh login next time
            self.fetch_error.emit(f"Authentication error: {exc}")
        except LibreLinkUpError as exc:
            self.fetch_error.emit(f"API error: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.fetch_error.emit(f"Network/unexpected error: {exc}")

    # ── internals ────────────────────────────────────────────────────────────

    def _ensure_client(self) -> None:
        """Create a new client and log in if we don't have one yet."""
        if self._client is None:
            self._client = LibreLinkUpClient(
                email=self._cfg["email"],
                password=self._cfg["password"],
                region=self._cfg.get("region", "eu"),
                patient_index=int(self._cfg.get("patient_index", 0)),
                patient_id=self._cfg.get("patient_id") or None,
            )
            self._client.login()

    def _fetch_with_reauth(self) -> list[dict[str, Any]]:
        """Fetch entries, re-logging in once if the token has expired."""
        try:
            return self._client.fetch_glucose_entries(  # type: ignore[union-attr]
                entries_to_fetch=int(self._cfg.get("entries_to_fetch", 36)),
                time_window_hours=int(self._cfg.get("time_window_hours", 3)),
            )
        except LibreLinkUpAuthError:
            log.info("llu_display: token expired, re-authenticating …")
            self._client = None
            self._ensure_client()
            return self._client.fetch_glucose_entries(  # type: ignore[union-attr]
                entries_to_fetch=int(self._cfg.get("entries_to_fetch", 36)),
                time_window_hours=int(self._cfg.get("time_window_hours", 3)),
            )


# ===========================================================================
# Glucose graph widget
# ===========================================================================

# Glucose-zone colours (matching the main NSOverlay palette)
_LOW_COLOR = "#ff4444"
_HIGH_COLOR = "#ffaa00"
_IN_RANGE_COLOR = "#44ff88"
_BG_COLOR = "#1a1a1a"


class LluGlucoseWidget(QWidget):
    """Standalone PyQt6 window that displays a LibreLink Up glucose graph."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__()
        self._cfg = dict(cfg)
        self._entries: list[dict[str, Any]] = []
        self._fetch_thread: _FetchThread | None = None

        self._init_ui()
        self._schedule_refresh()

    # ── UI construction ──────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        self.setWindowTitle("LibreLink Up — Glucose Graph")
        self.resize(
            int(self._cfg.get("widget_width", 520)),
            int(self._cfg.get("widget_height", 320)),
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        # Header row: current reading label + status
        header_row = QVBoxLayout()
        self._lbl_reading = QLabel("—")
        self._lbl_reading.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        self._lbl_reading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_reading.setStyleSheet("color: #ffffff;")

        self._lbl_status = QLabel("Connecting…")
        self._lbl_status.setFont(QFont("Arial", 10))
        self._lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_status.setStyleSheet("color: #aaaaaa;")

        header_row.addWidget(self._lbl_reading)
        header_row.addWidget(self._lbl_status)
        outer.addLayout(header_row)

        # pyqtgraph plot
        pg.setConfigOption("background", _BG_COLOR)
        pg.setConfigOption("foreground", "#cccccc")

        self._plot_widget = pg.PlotWidget(
            axisItems={"bottom": _TimeAxisItem(orientation="bottom")}
        )
        self._plot_widget.setLabel("left", "Glucose (mg/dL)")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self._plot_widget.setMenuEnabled(False)
        outer.addWidget(self._plot_widget, stretch=1)

        # Bottom info row
        self._lbl_info = QLabel(self._info_text())
        self._lbl_info.setFont(QFont("Arial", 9))
        self._lbl_info.setStyleSheet("color: #888888;")
        self._lbl_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._lbl_info)

        self.setStyleSheet(f"background-color: {_BG_COLOR};")

    def _info_text(self) -> str:
        x = int(self._cfg.get("entries_to_fetch", 36))
        w = int(self._cfg.get("time_window_hours", 3))
        return (
            f"Showing up to {x} entries within the last {w} hour{'s' if w > 1 else ''} "
            "— right-click for settings"
        )

    # ── Refresh scheduling ────────────────────────────────────────────────────

    def _schedule_refresh(self) -> None:
        """Kick off the first fetch immediately, then on a timer."""
        self._timer = QTimer(self)
        self._timer.setInterval(int(self._cfg.get("refresh_interval_ms", 60_000)))
        self._timer.timeout.connect(self._start_fetch)
        self._timer.start()
        # Immediate first fetch
        QTimer.singleShot(0, self._start_fetch)

    def _start_fetch(self) -> None:
        if self._fetch_thread and self._fetch_thread.isRunning():
            return  # previous fetch still in progress

        self._lbl_status.setText("Fetching…")
        self._fetch_thread = _FetchThread(self._cfg, parent=self)
        self._fetch_thread.result_ready.connect(self._on_result)
        self._fetch_thread.fetch_error.connect(self._on_error)
        self._fetch_thread.start()

    # ── Data callbacks ────────────────────────────────────────────────────────

    def _on_result(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            self._lbl_status.setText("No data in selected time window.")
            self._lbl_reading.setText("—")
            return

        self._entries = entries
        self._redraw()

        latest = entries[-1]
        value = latest.get("ValueInMgPerDl", "—")
        trend = _trend_arrow(latest.get("TrendArrow"))
        color = self._glucose_color(value)
        self._lbl_reading.setStyleSheet(f"color: {color};")
        self._lbl_reading.setText(f"{value} {trend}" if trend else str(value))
        self._lbl_status.setText(
            f"Updated at {_ts_to_local_str(latest.get('_ts', 0))} "
            f"· {len(entries)} point{'s' if len(entries) != 1 else ''}"
        )

    def _on_error(self, message: str) -> None:
        log.error("llu_display: %s", message)
        self._lbl_status.setText(f"Error: {message}")

    # ── Graph rendering ───────────────────────────────────────────────────────

    def _redraw(self) -> None:
        self._plot_widget.clear()

        if not self._entries:
            return

        xs = [e["_ts"] for e in self._entries]
        ys = [e.get("ValueInMgPerDl", 0) for e in self._entries]

        low = float(self._cfg.get("target_low", 70))
        high = float(self._cfg.get("target_high", 180))

        # Target-zone shading
        if xs:
            x_min, x_max = xs[0] - 60, xs[-1] + 60
            low_zone = pg.LinearRegionItem(
                [0, low],
                orientation="horizontal",
                movable=False,
                brush=pg.mkBrush(255, 68, 68, 20),
                pen=pg.mkPen(None),
            )
            high_zone = pg.LinearRegionItem(
                [high, 400],
                orientation="horizontal",
                movable=False,
                brush=pg.mkBrush(255, 170, 0, 20),
                pen=pg.mkPen(None),
            )
            self._plot_widget.addItem(low_zone)
            self._plot_widget.addItem(high_zone)

            # Target-range reference lines
            self._plot_widget.addItem(
                pg.InfiniteLine(
                    pos=low, angle=0,
                    pen=pg.mkPen(_LOW_COLOR, width=1, style=Qt.PenStyle.DashLine),
                )
            )
            self._plot_widget.addItem(
                pg.InfiniteLine(
                    pos=high, angle=0,
                    pen=pg.mkPen(_HIGH_COLOR, width=1, style=Qt.PenStyle.DashLine),
                )
            )

        # Glucose line
        self._plot_widget.plot(
            xs, ys,
            pen=pg.mkPen("#44aaff", width=2),
            connect="finite",
        )

        # Colour-coded scatter dots
        colors = [QColor(self._glucose_color(y)) for y in ys]
        brushes = [pg.mkBrush(c) for c in colors]
        scatter = pg.ScatterPlotItem(
            x=xs, y=ys,
            size=7,
            brush=brushes,
            pen=pg.mkPen("#000000", width=1),
        )
        self._plot_widget.addItem(scatter)

        # Fit the view to the data
        x_padding = 120
        self._plot_widget.setXRange(xs[0] - x_padding, xs[-1] + x_padding, padding=0)
        y_margin = 20
        self._plot_widget.setYRange(
            max(0, min(ys) - y_margin),
            max(ys) + y_margin,
            padding=0,
        )

    # ── Context menu ──────────────────────────────────────────────────────────

    def contextMenuEvent(self, event) -> None:
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #2a2a2a; color: #eee; border: 1px solid #555; }"
            "QMenu::item:selected { background: #444; }"
        )
        settings_act = menu.addAction("Settings / Edit Connection…")
        refresh_act = menu.addAction("Refresh Now")
        menu.addSeparator()
        quit_act = menu.addAction("Quit")

        action = menu.exec(event.globalPos())
        if action == settings_act:
            self._open_settings()
        elif action == refresh_act:
            self._start_fetch()
        elif action == quit_act:
            QApplication.quit()

    def _open_settings(self) -> None:
        dlg = _SetupDialog(self._cfg, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_cfg = dlg.get_values()
            if not new_cfg.get("email") or not new_cfg.get("password"):
                QMessageBox.warning(self, "Missing credentials",
                                    "Please enter your e-mail and password.")
                return
            self._cfg = new_cfg
            _save_config(self._cfg)
            self._lbl_info.setText(self._info_text())
            # Force a fresh login on next fetch
            if self._fetch_thread:
                self._fetch_thread._client = None  # noqa: SLF001
            self._timer.setInterval(int(self._cfg.get("refresh_interval_ms", 60_000)))
            self._start_fetch()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _glucose_color(self, value: Any) -> str:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "#ffffff"
        low = float(self._cfg.get("target_low", 70))
        high = float(self._cfg.get("target_high", 180))
        if v < low:
            return _LOW_COLOR
        if v > high:
            return _HIGH_COLOR
        return _IN_RANGE_COLOR


# ===========================================================================
# pyqtgraph custom axis: HH:MM labels
# ===========================================================================

class _TimeAxisItem(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        from datetime import datetime as _dt
        result = []
        for v in values:
            try:
                result.append(_dt.fromtimestamp(v).strftime("%H:%M"))
            except (ValueError, OSError):
                result.append("")
        return result


# ===========================================================================
# Utility helpers
# ===========================================================================

_TREND_ARROWS = {
    1: "↑↑",
    2: "↑",
    3: "↗",
    4: "→",
    5: "↘",
    6: "↓",
    7: "↓↓",
}


def _trend_arrow(code: Any) -> str:
    try:
        return _TREND_ARROWS.get(int(code), "")
    except (TypeError, ValueError):
        return ""


def _ts_to_local_str(ts: float) -> str:
    from datetime import datetime as _dt
    try:
        return _dt.fromtimestamp(ts).strftime("%H:%M:%S")
    except (ValueError, OSError):
        return "—"


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("LLU Glucose Display")

    cfg = _load_config()

    # First-run wizard: prompt for credentials if none are stored
    if not cfg.get("email") or not cfg.get("password"):
        dlg = _SetupDialog(cfg)
        dlg.setWindowTitle("LibreLink Up — First-Time Setup")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        cfg = dlg.get_values()
        if not cfg.get("email") or not cfg.get("password"):
            QMessageBox.critical(
                None, "Missing credentials",
                "E-mail and password are required to use LibreLink Up.\n"
                "Please run the application again and fill in all fields."
            )
            sys.exit(1)
        _save_config(cfg)

    widget = LluGlucoseWidget(cfg)
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
