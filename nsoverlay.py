import sys
import os
import json
import hashlib
import logging
import logging.handlers
import requests
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QMenu,
                             QDialog, QFrame, QGraphicsDropShadowEffect,
                             QSystemTrayIcon, QMessageBox)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import QFont, QColor, QAction, QFontMetrics, QPixmap, QPainter, QIcon
import pyqtgraph as pg
from pyqtgraph.graphicsItems.ViewBox import ViewBox
from src.core import DateTimeParser, load_config as core_load_config
from src.data import RemoteFetchThread, NightscoutTreatmentWriteThread, TreatmentWriteRequest
from src.graph import TimeAxisItem
from src.ui import SetupWizard, SettingsDialog, TreatmentDialog

# Resolve paths relative to the executable/script location so the app works
# correctly when launched from the taskbar or a shortcut.
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(_BASE_DIR, "config.json")
POSITION_FILE = os.path.join(_BASE_DIR, "widget_position.json")
ZOOM_FILE = os.path.join(_BASE_DIR, "zoom_state.json")
LOG_FILE = os.path.join(_BASE_DIR, "nsoverlay.log")

# ── Logger setup ──────────────────────────────────────────────────────────────
def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("nsoverlay")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger  # already configured (e.g. reloaded module)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler: 1 MB per file, keep last 3 files.
    # Only WARNING and above are written to disk — DEBUG stays in the terminal.
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.WARNING)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Mirror everything (including DEBUG) to stdout (visible in the Python terminal)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger

log = _setup_logger()

BOLUS_COLOR = '#1E90FF'

INSULIN_TYPE_MODEL_FACTORS = {
    "Humalog Lispro": {"dia_scale": 1.00, "peak_scale": 1.00, "onset_scale": 1.00},
    "Novolog Aspart": {"dia_scale": 1.00, "peak_scale": 1.00, "onset_scale": 1.00},
    "Fiasp": {"dia_scale": 0.92, "peak_scale": 0.78, "onset_scale": 0.65},
    "Lyumjev": {"dia_scale": 0.90, "peak_scale": 0.75, "onset_scale": 0.60},
    "Apidra Glulisine": {"dia_scale": 0.96, "peak_scale": 0.95, "onset_scale": 0.90},
}

INSULIN_TYPE_ALIASES = {
    "humalog": "Humalog Lispro",
    "humalog lispro": "Humalog Lispro",
    "lispro": "Humalog Lispro",
    "insulin lispro": "Humalog Lispro",
    "novolog": "Novolog Aspart",
    "aspart": "Novolog Aspart",
    "fiasp": "Fiasp",
    "lyumjev": "Lyumjev",
    "apidra": "Apidra Glulisine",
    "glulisine": "Apidra Glulisine",
}

CURVE_TO_INSULIN_TYPE = {
    "rapid-acting": "Humalog Lispro",
    "ultra-rapid": "Fiasp",
}


def _normalize_insulin_type_name(raw_value):
    if not isinstance(raw_value, str):
        return "Humalog Lispro"

    candidate = raw_value.strip()
    if not candidate:
        return "Humalog Lispro"

    return INSULIN_TYPE_ALIASES.get(candidate.lower(), candidate)


# ── Stylesheet loader ─────────────────────────────────────────────────────────
def _load_qss(filename: str) -> str:
    path = os.path.join(_BASE_DIR, "styles", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        log.warning("Stylesheet not found: %s", path)
        return ""


DARK_QSS         = _load_qss("dark.qss")
CONTEXT_MENU_QSS = _load_qss("context_menu.qss")


def load_config():
    return core_load_config(CONFIG_FILE)


# ── GlucoseWidget Main Application ─────────────────────────────────────────────

class GlucoseWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("NSOverlay")
        _icon_path = os.path.join(_BASE_DIR, "icon.ico")
        if os.path.exists(_icon_path):
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(_icon_path))

        self.nightscout_url, self.api_secret, self.api_secret_raw, self.config = load_config()
        self.timezone_offset = self.config['timezone_offset']
        self._profile_iob_settings = {}
        self.setObjectName("GlucoseWidget")
        
        # Initialize utility managers
        self._datetime_parser = DateTimeParser()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        # Required for graph background transparency to show through
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # Store original configured dimensions
        self.base_width = self.config['widget_width']
        self.base_height = self.config['widget_height']
        self.user_resized = False
        self.setMinimumSize(200, 150)
        self.setMaximumSize(800, 600)
        
        self.resize(self.base_width, self.base_height)
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(8, 8, 8, 8)
        self.main_layout.setSpacing(0)
        self.setLayout(self.main_layout)

        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(10, 6, 10, 4)
        self.header_layout.setSpacing(12)
        
        self.left_info_layout = QVBoxLayout()
        self.left_info_layout.setContentsMargins(0, 0, 0, 0)
        self.left_info_layout.setSpacing(1)
        
        self.time_label = QLabel("")
        self.time_label.setObjectName("HeaderTime")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setFont(QFont("sans-serif", self.config['time_font_size'], QFont.Weight.Medium))
        
        self.age_label = QLabel("")
        self.age_label.setObjectName("HeaderAge")
        self.age_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.age_label.setFont(QFont("sans-serif", self.config['age_font_size'] - 1, QFont.Weight.Normal))
        
        self.left_info_layout.addWidget(self.time_label)
        self.left_info_layout.addWidget(self.age_label)
        
        self.label = QLabel("Loading...")
        self.label.setObjectName("HeaderGlucose")
        self.label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.label.setFont(QFont("sans-serif", self.config['glucose_font_size'], QFont.Weight.Bold))

        # Vertical divider between time/age and glucose
        self.header_sep = QFrame()
        self.header_sep.setObjectName("HeaderSeparator")
        self.header_sep.setFrameShape(QFrame.Shape.VLine)
        self.header_sep.setFrameShadow(QFrame.Shadow.Plain)
        self.header_sep.setFixedWidth(1)
        
        # Pills container – sits on the left edge of the header row, stacked vertically
        self.pills_layout = QVBoxLayout()
        self.pills_layout.setContentsMargins(0, 0, 0, 0)
        self.pills_layout.setSpacing(3)
        self.header_pills_widget = QWidget()
        self.header_pills_widget.setObjectName("HeaderPillsContainer")
        self.header_pills_widget.setLayout(self.pills_layout)
        self.header_pills_widget.hide()

        self.header_layout.addWidget(self.header_pills_widget, 0)
        self.header_layout.addStretch(1)
        self.header_layout.addLayout(self.left_info_layout, 0)
        self.header_layout.addStretch(1)
        self.header_layout.addWidget(self.header_sep, 0)
        self.header_layout.addWidget(self.label, 0)
        
        self.header_bar = QWidget()
        self.header_bar.setObjectName("HeaderBar")
        self.header_bar.setLayout(self.header_layout)
        self.header_bar.setFixedHeight(58)
        self.main_layout.addWidget(self.header_bar)

        self.close_button = QLabel("✕", self)
        self.close_button.setObjectName("CloseButton")
        self.close_button.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.close_button.setFont(QFont("sans-serif", 16, QFont.Weight.Bold))
        self.close_button.setFixedSize(34, 34)
        self.close_button.hide()
        self.close_button.mousePressEvent = lambda ev: (self.close(), None)[1]  # type: ignore[method-assign]
        self._apply_close_button_style(False)

        self.setup_graph()
        self._apply_header_label_styles()
        self._apply_header_background()
        self._apply_dynamic_header_fonts()
        self.setup_shortcuts()
        self._has_visible_pills = False
        self._tray_pill_texts = []

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_glucose)
        self.timer.start(self.config['refresh_interval'])
        
        self.time_timer = QTimer()
        self.time_timer.timeout.connect(self.update_time_display)
        self.time_timer.start(1000)

        self.load_position()
        self.load_zoom_state()
        # Defer the first fetch until after the widget is shown and the event loop
        # has processed the first paint — this ensures the graph viewport is fully
        # laid out so viewRange(), dot sizing and treatment positions are correct.
        QTimer.singleShot(0, self.update_glucose)

        self.old_pos = None
        self.last_entry_time = None
        self._entries_cache = []   # in-memory store of raw entry dicts, sorted oldest-first
        self._treatments_cache = []  # in-memory store of treatment dicts, sorted oldest-first
        self._fetch_thread = RemoteFetchThread(None, self)
        self._fetch_thread.resultReady.connect(self._on_remote_fetch_result)
        self._fetch_thread.fetchError.connect(self._on_remote_fetch_error)
        self._fetch_thread.start()
        self._last_render_key = None
        self._last_treatment_render_key = None
        self._last_header_pill_render_key = None
        self._last_time_text = None
        self._last_age_text = None
        self._last_age_color = None
        self._last_glucose_stale_state = None
        self._last_tray_icon_key = None
        self._last_tray_icon = None
        self._brush_cache = {}
        self._pen_cache = {}
        self._pending_treatment_threads = set()
        self.resize_edge = None
        self.resize_start_pos = None
        self.resize_start_geometry = None
        self.resize_edge_margin = 16
        
        # Position saving timer to avoid excessive saves during dragging
        self.position_save_timer = QTimer()
        self.position_save_timer.setSingleShot(True)
        self.position_save_timer.timeout.connect(self.save_position_and_size)
        self.position_save_timer.setInterval(500)

        # Poll mouse position every 100 ms to show/hide the close button.
        # enterEvent/leaveEvent are unreliable when WA_TranslucentBackground is set
        # because Windows skips mouse events over fully-transparent pixels.
        self.hover_poll_timer = QTimer()
        self.hover_poll_timer.timeout.connect(self._poll_hover)
        self.hover_poll_timer.start(100)
        
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.screenAdded.connect(self.validate_position_on_screen_change)
            app.screenRemoved.connect(self.validate_position_on_screen_change)
            for screen in app.screens():
                screen.geometryChanged.connect(self.validate_position_on_screen_change)

        self._setup_tray()

        self._profile_sync_timer = QTimer()
        self._profile_sync_timer.timeout.connect(self._sync_iob_settings_from_nightscout)
        self._profile_sync_timer.start(30 * 60 * 1000)
        QTimer.singleShot(0, self._sync_iob_settings_from_nightscout)

    def _parse_ns_datetime(self, value):
        """Parse Nightscout ISO timestamps and return naive UTC datetime."""
        if not value:
            return None
        return self._datetime_parser.parse(str(value).strip())

    def _select_active_profile_entry(self, payload):
        if isinstance(payload, list):
            candidates = [item for item in payload if isinstance(item, dict)]
            if not candidates:
                return None

            def _entry_sort_key(entry):
                mills = entry.get('mills')
                if isinstance(mills, (int, float)):
                    return float(mills)
                created = self._parse_ns_datetime(entry.get('startDate') or entry.get('created_at') or "")
                if created is not None:
                    return created.timestamp() * 1000
                return 0.0

            return sorted(candidates, key=_entry_sort_key, reverse=True)[0]

        if isinstance(payload, dict):
            return payload
        return None

    def _extract_profile_iob_settings(self, entry):
        if not isinstance(entry, dict):
            return {}

        store_raw = entry.get('store')
        store = store_raw if isinstance(store_raw, dict) else {}
        profile_name = entry.get('defaultProfile')
        profile = store.get(profile_name) if isinstance(profile_name, str) else None
        if not isinstance(profile, dict) and store:
            first_key = next(iter(store.keys()), None)
            profile = store.get(first_key) if first_key else None
        if not isinstance(profile, dict):
            profile = {}

        extracted = {}

        dia_value = profile.get('dia')
        if isinstance(dia_value, (int, float)):
            extracted['iob_dia_hours'] = max(2.0, min(12.0, float(dia_value)))

        peak_value = profile.get('insulinPeakTime', profile.get('peak', profile.get('peakTime')))
        if isinstance(peak_value, (int, float)):
            extracted['iob_peak_minutes'] = max(30, min(180, int(peak_value)))

        onset_value = profile.get('insulinOnsetTime', profile.get('onset', profile.get('onsetTime')))
        if isinstance(onset_value, (int, float)):
            extracted['iob_onset_minutes'] = max(0, min(60, int(onset_value)))

        insulin_type = _normalize_insulin_type_name(
            profile.get('insulinType', CURVE_TO_INSULIN_TYPE.get(str(profile.get('curve', '')).strip().lower(), ''))
        )
        if insulin_type:
            extracted['default_insulin_type'] = insulin_type

        return extracted

    def _sync_iob_settings_from_nightscout(self):
        if not self.nightscout_url or not self.api_secret:
            return

        try:
            url = f"{self.nightscout_url.rstrip('/')}/api/v1/profile.json"
            response = requests.get(
                url,
                headers={"api-secret": self.api_secret},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            active_entry = self._select_active_profile_entry(payload)
            extracted = self._extract_profile_iob_settings(active_entry)
            if extracted:
                self._profile_iob_settings = extracted
        except Exception as exc:
            log.debug("Nightscout profile sync failed: %s", exc)
        
    def resizeEvent(self, event):
        """Position the close button in top-right corner when widget is resized"""
        super().resizeEvent(event)
        if hasattr(self, 'close_button'):
            self.close_button.move(self.width() - 38, 3)
        self._apply_dynamic_header_fonts()
        self._apply_responsive_header_layout()
        
        if hasattr(self, 'user_resized') and event.oldSize().isValid():
            old_size = event.oldSize()
            new_size = event.size()
            if abs(old_size.width() - new_size.width()) > 5 or abs(old_size.height() - new_size.height()) > 5:
                self.user_resized = True
        
        if hasattr(self, 'position_save_timer'):
            self.position_save_timer.start()
    
    def _poll_hover(self):
        """Show/hide close button based on whether the cursor is inside the widget.
        Needed because enterEvent/leaveEvent miss transparent-pixel areas."""
        from PyQt6.QtGui import QCursor
        cursor_pos = self.mapFromGlobal(QCursor.pos())
        inside = self.rect().contains(cursor_pos)
        if not (self.resize_edge or self.old_pos):
            self._update_hover_cursor(cursor_pos)
        if hasattr(self, 'close_button'):
            if inside:
                if not self.close_button.isVisible():
                    self.close_button.show()
                    self.close_button.raise_()
                self._apply_close_button_style(True)
            elif self.close_button.isVisible():
                self.close_button.hide()
                self._apply_close_button_style(False)
            else:
                self._apply_close_button_style(False)

    def _apply_close_button_style(self, hovered=False):
        """Apply close button colors from appearance settings."""
        if not hasattr(self, 'close_button'):
            return
        ui_colors = self.config.get('appearance', {}).get('colors', {}).get('ui', {})
        text_color = ui_colors.get('close_button_hover', '#ff6666') if hovered else ui_colors.get('close_button', '#ff4444')
        bg_color = (
            ui_colors.get('close_button_hover_background', 'rgba(255,68,68,200)')
            if hovered else ui_colors.get('close_button_background', 'rgba(0,0,0,150)')
        )
        self.close_button.setStyleSheet(
            f"color: {text_color}; background-color: {bg_color}; border-radius: 17px;"
        )

    def _update_hover_cursor(self, pos):
        if self.resize_edge or self.old_pos:
            return

        edge = self.get_resize_edge(pos)
        cursor = self.get_resize_cursor(edge)
        if self.cursor().shape() != cursor:
            self.setCursor(cursor)

    def _apply_responsive_header_layout(self):
        """Adapt header density for narrow widget sizes to preserve readability."""
        w = self.width()
        compact = w < 360

        self.header_layout.setSpacing(8 if compact else 12)
        self.header_layout.setContentsMargins(8 if compact else 10, 6, 8 if compact else 10, 4)

        if hasattr(self, 'header_sep'):
            self.header_sep.setVisible(w >= 280)

        if hasattr(self, 'header_pills_widget'):
            # Keep pills visible even on compact widths; text is already elided.
            self.header_pills_widget.setVisible(self._has_visible_pills)

    def _apply_dynamic_header_fonts(self):
        """Scale header typography for compact widths while respecting configured sizes."""
        w = self.width()
        glucose_base = max(8, int(self.config.get('glucose_font_size', 18)))
        time_base = max(6, int(self.config.get('time_font_size', 12)))
        age_base = max(6, int(self.config.get('age_font_size', 10)) - 1)

        if w < 280:
            glucose_size = max(10, glucose_base - 4)
            time_size = max(8, time_base - 2)
            age_size = max(7, age_base - 2)
        elif w < 340:
            glucose_size = max(11, glucose_base - 2)
            time_size = max(9, time_base - 1)
            age_size = max(8, age_base - 1)
        else:
            glucose_size = glucose_base
            time_size = time_base
            age_size = age_base

        self.label.setFont(QFont("sans-serif", glucose_size, QFont.Weight.Bold))
        self.time_label.setFont(QFont("sans-serif", time_size, QFont.Weight.Medium))
        self.age_label.setFont(QFont("sans-serif", age_size, QFont.Weight.Normal))

    def enterEvent(self, event):
        super().enterEvent(event)
        if hasattr(self, 'close_button'):
            self.close_button.raise_()
    
    def leaveEvent(self, event):
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts"""
        if event.key() == Qt.Key.Key_Escape or event.key() == Qt.Key.Key_Q:
            self.close()
        super().keyPressEvent(event)

    def _update_graph_background(self):
        """Apply graph background color with configured opacity (0-100)."""
        appearance = self.config['appearance']
        slider_opacity = int(appearance.get('graph_background_opacity', 100))
        opacity = slider_opacity if appearance.get('transparency_enabled', True) else 100
        graph_colors = appearance.get('colors', {}).get('graph', {})
        bg_color = graph_colors.get('background', appearance.get('background_color', '#1a1a1a'))
        color = QColor(bg_color)
        color.setAlpha(round(opacity / 100 * 255))
        self.graph.setBackground(color)

    def _apply_widget_background(self):
        """Apply widget background color with configured opacity (matches graph opacity).
        Uses the object-name selector so the rule does NOT cascade to child widgets."""
        appearance = self.config['appearance']
        slider_opacity = int(appearance.get('graph_background_opacity', 100))
        opacity = slider_opacity if appearance.get('transparency_enabled', True) else 100
        widget_bg = self.config['appearance']['colors']['ui']['widget_background']
        color = QColor(widget_bg)
        alpha = round(opacity / 100 * 255)
        rgba = f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        self.setStyleSheet(f"#GlucoseWidget {{ background-color: {rgba}; border-radius: 10px; }}")

    def _apply_header_background(self):
        """Apply header bar background color with same opacity as the graph."""
        appearance = self.config['appearance']
        slider_opacity = int(appearance.get('graph_background_opacity', 100))
        opacity = slider_opacity if appearance.get('transparency_enabled', True) else 100
        header_bg = appearance.get('colors', {}).get('ui', {}).get('header_background', '#0d0d0d')
        color = QColor(header_bg)
        alpha = round(opacity / 100 * 255)
        rgba = f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        self.header_bar.setStyleSheet(f"#HeaderBar {{ background-color: {rgba}; }}")

    def _update_header_pills(self, treatments):
        """Rebuild header pill labels based on the latest treatments and current config.

        Each entry in config['header_pills'] may have:
                    event_type    (str, required)  – matches treatment eventType (case-insensitive)
                    event_types   (list[str])      – optional list of event types; overrides event_type
                                                                                     when present and non-empty
          label         (str)            – display label; defaults to event_type
          show_field    (str)            – treatment field whose value is shown/summed
          suffix        (str)            – text appended after the value (e.g. "U", "g")
          color         (str)            – pill background color (default "#4a9eff")
          max_age_hours (number)         – how old the treatment may be (default 24); ignored when sum_daily=true
          sum_daily     (bool)           – when true, sum show_field across all matching treatments
                                           on the current local day (uses timezone_offset)
        """
        pill_configs = self.config.get('header_pills', [])
        pill_render_key = self._build_header_pill_render_key(treatments)
        if pill_render_key == self._last_header_pill_render_key:
            return

        pill_texts = []

        # Clear all items from pills layout
        while self.pills_layout.count():
            item = self.pills_layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()

        if not pill_configs:
            self._has_visible_pills = False
            self._tray_pill_texts = []
            self.header_pills_widget.hide()
            if hasattr(self, '_last_tray_glucose'):
                self._refresh_tray_tooltip(
                    self._last_tray_glucose,
                    self._last_tray_trend,
                    self._last_tray_delta,
                    bool(getattr(self, '_tray_was_stale', False)),
                )
            return

        now_utc = datetime.utcnow()
        local_today = (now_utc + timedelta(hours=self.timezone_offset)).date()

        def _shadow():
            fx = QGraphicsDropShadowEffect()
            fx.setBlurRadius(6)
            fx.setOffset(0, 0)
            fx.setColor(QColor(0, 0, 0, 200))
            return fx

        treatments_by_event = {}
        for treatment in treatments:
            event_type = treatment.get('eventType', '').lower()
            if not event_type:
                continue
            created_utc = self._parse_ns_datetime(treatment.get('created_at', ''))
            treatments_by_event.setdefault(event_type, []).append((treatment, created_utc))

        def _event_keys_for_pill(pill_cfg):
            event_types = pill_cfg.get('event_types')
            keys = []

            if isinstance(event_types, list):
                for event_type in event_types:
                    if isinstance(event_type, str):
                        normalized = event_type.strip().lower()
                        if normalized and normalized not in keys:
                            keys.append(normalized)

            if not keys:
                event_type_cfg = pill_cfg.get('event_type', '')
                if isinstance(event_type_cfg, str):
                    separators = ['|', ',']
                    expanded = [event_type_cfg]
                    for sep in separators:
                        if any(sep in part for part in expanded):
                            next_parts = []
                            for part in expanded:
                                next_parts.extend(part.split(sep))
                            expanded = next_parts
                    for raw_key in expanded:
                        normalized = raw_key.strip().lower()
                        if normalized and normalized not in keys:
                            keys.append(normalized)

            return keys

        pills_added = 0

        for pill_cfg in pill_configs:
            if not bool(pill_cfg.get('enabled', True)):
                continue

            event_keys = _event_keys_for_pill(pill_cfg)
            if not event_keys:
                continue

            matching_treatments = []
            seen_treatment_ids = set()
            for event_key in event_keys:
                for treatment, treatment_time in treatments_by_event.get(event_key, []):
                    treatment_id = treatment.get('_id')
                    unique_key = treatment_id if treatment_id is not None else id(treatment)
                    if unique_key in seen_treatment_ids:
                        continue
                    seen_treatment_ids.add(unique_key)
                    matching_treatments.append((treatment, treatment_time))

            if not matching_treatments:
                continue

            default_label = pill_cfg.get('event_type', '') or '/'.join(event_keys)
            label_text = pill_cfg.get('label', default_label)
            show_field = pill_cfg.get('show_field')
            show_fields = pill_cfg.get('show_fields', [])
            suffix = pill_cfg.get('suffix', '')
            suffix_map = pill_cfg.get('suffix_map', {})  # e.g., {"insulin": "U", "carbs": "g"}
            sum_daily = bool(pill_cfg.get('sum_daily', False))
            max_age_hours = float(pill_cfg.get('max_age_hours', 24))

            # Normalize show_field and show_fields into a list
            if show_fields and isinstance(show_fields, list):
                fields_list = show_fields
            elif show_field:
                if isinstance(show_field, list):
                    fields_list = show_field
                else:
                    fields_list = [show_field]
            else:
                continue

            # Helper to get suffix for a field
            def get_field_suffix(field):
                if isinstance(suffix_map, dict) and field in suffix_map:
                    return suffix_map[field]
                return suffix

            value_str = ''

            if sum_daily:
                # Sum each field for all matching treatments on the current local day
                field_totals = {field: 0.0 for field in fields_list}
                found_any = False
                for t, t_time in matching_treatments:
                    if t_time is None:
                        continue
                    t_local_date = (t_time + timedelta(hours=self.timezone_offset)).date()
                    if t_local_date != local_today:
                        continue
                    for field in fields_list:
                        raw_val = t.get(field)
                        try:
                            field_totals[field] += float(raw_val)
                            found_any = True
                        except (TypeError, ValueError):
                            pass
                if not found_any:
                    continue
                # Build value string with all fields
                value_parts = []
                for field in fields_list:
                    display_val = int(field_totals[field]) if field_totals[field] == int(field_totals[field]) else round(field_totals[field], 1)
                    field_suffix = get_field_suffix(field)
                    value_parts.append(f"{display_val}{field_suffix}")
                value_str = f": {' / '.join(value_parts)}"
            else:
                # Find most recent matching treatment within max_age_hours
                best_treatment = None
                best_time = None
                for t, t_time in reversed(matching_treatments):
                    if t_time is None:
                        continue
                    age_hours = (now_utc - t_time).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        continue
                    if best_time is None or t_time > best_time:
                        best_time = t_time
                        best_treatment = t
                if best_treatment is None:
                    continue
                # Get all field values from the best treatment
                value_parts = []
                for field in fields_list:
                    val = best_treatment.get(field)
                    if val is not None:
                        field_suffix = get_field_suffix(field)
                        value_parts.append(f"{val}{field_suffix}")
                if value_parts:
                    value_str = f": {' / '.join(value_parts)}"

            pill_text = f"{label_text}{value_str}"
            pill_texts.append(pill_text)

            pill_label = QLabel(pill_text)
            is_bold = bool(pill_cfg.get('bold', False))
            pill_weight = QFont.Weight.Bold if is_bold else QFont.Weight.Medium
            pill_label.setFont(QFont("sans-serif", self.config['age_font_size'] - 1, pill_weight))
            pill_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            max_pill_width = max(88, min(170, int((max(self.width(), 300) - 180) / 2)))
            metrics = QFontMetrics(pill_label.font())
            display_text = metrics.elidedText(pill_text, Qt.TextElideMode.ElideRight, max_pill_width)
            pill_label.setText(display_text)
            if display_text != pill_text:
                pill_label.setToolTip(pill_text)
            pill_label.setMaximumWidth(max_pill_width + 20)
            pill_label.setMinimumHeight(24)

            pill_color = pill_cfg.get('color', '#80e8e0')
            pill_label.setStyleSheet(
                f"color: {pill_color}; background-color: transparent; padding: 0px 8px;"
            )
            pill_label.setGraphicsEffect(_shadow())

            self.pills_layout.addWidget(pill_label)
            pills_added += 1

        if pills_added > 0:
            self._has_visible_pills = True
            self._tray_pill_texts = pill_texts
            self._apply_responsive_header_layout()
        else:
            self._has_visible_pills = False
            self._tray_pill_texts = []
            self.header_pills_widget.hide()

        if hasattr(self, '_last_tray_glucose'):
            self._refresh_tray_tooltip(
                self._last_tray_glucose,
                self._last_tray_trend,
                self._last_tray_delta,
                bool(getattr(self, '_tray_was_stale', False)),
            )

        self._last_header_pill_render_key = pill_render_key

    def _apply_header_label_styles(self, glucose_text_color=None):
        """Style the unified header bar and its child labels."""
        ui_colors = self.config['appearance']['colors']['ui']
        time_color = ui_colors['time_label']
        age_color = ui_colors['age_label']
        text_color = glucose_text_color or ui_colors['main_glucose_text']
        appearance = self.config['appearance']
        slider_pill_opacity = int(appearance.get('label_pill_opacity', 40))
        pill_opacity_pct = slider_pill_opacity if appearance.get('transparency_enabled', True) else 100
        pill_alpha = round(pill_opacity_pct / 100 * 255)
        bg_hex = self.config['appearance'].get('background_color', '#1a1a1a')
        _bg = QColor(bg_hex)
        bar_bg = f"rgba({_bg.red()}, {_bg.green()}, {_bg.blue()}, {pill_alpha})"

        def _shadow():
            fx = QGraphicsDropShadowEffect()
            fx.setBlurRadius(4)
            fx.setOffset(0, 0)
            fx.setColor(QColor(0, 0, 0, 160))
            return fx

        # Single unified bar — #HeaderBar scopes the rule so children stay transparent
        self.header_bar.setStyleSheet(
            f"#HeaderBar {{ background-color: {bar_bg}; "
            f"border-top-left-radius: 12px; border-top-right-radius: 12px; "
            f"border: 1px solid rgba(96, 118, 150, 90); "
            f"border-bottom: none; }}"
        )

        self.time_label.setStyleSheet(
            f"color: {time_color}; background-color: transparent; padding: 0px 4px;"
        )
        self.time_label.setGraphicsEffect(_shadow())

        self.age_label.setStyleSheet(
            f"color: {age_color}; background-color: transparent; padding: 0px 4px;"
        )
        self.age_label.setGraphicsEffect(_shadow())

        self.header_sep.setStyleSheet(
            "background: rgba(142, 162, 196, 120); border: none; margin: 6px 6px;"
        )

        self.label.setStyleSheet(
            f"color: {text_color}; background-color: transparent; "
            f"font-weight: bold; padding: 0px 4px;"
        )
        self.label.setGraphicsEffect(_shadow())

    def setup_graph(self):
        self.graph = pg.PlotWidget(axisItems={'bottom': TimeAxisItem(orientation='bottom')})
        self._update_graph_background()
        self.graph.setMinimumHeight(150)
        self.graph.setMouseTracking(True)
        self.graph.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.graph.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        
        self.graph.setStyleSheet("""  
            QWidget {
                border: none;
                background-color: transparent;
            }
        """)
        
        self.graph.setContentsMargins(2, 2, 2, 2)
        
        self.graph.setMouseEnabled(x=True, y=True)
        self.graph.enableAutoRange(axis='x')
        
        self.graph.mouseDoubleClickEvent = self.center_graph

        # Hide pyqtgraph's built-in "A" auto-range button
        plot_item = self.graph.getPlotItem()
        if plot_item is not None:
            plot_item.hideButtons()

        self.current_y_range = None
        
        axis_color = self.config['appearance']['colors']['graph']['axis_lines']
        axis_text_color = self.config['appearance']['colors']['graph']['axis_text']
        
        self.graph.showGrid(x=True, y=True, alpha=self.config['appearance']['grid_opacity'])
        # Push the grid behind all data items
        plot_item = self.graph.getPlotItem()
        if plot_item is not None:
            for item in plot_item.items:
                if type(item).__name__ == 'GridItem':
                    item.setZValue(-10)
                    break
        self.graph.getAxis('left').setPen(pg.mkPen(color=axis_color, width=1))
        self.graph.getAxis('bottom').setPen(pg.mkPen(color=axis_color, width=1))
        self.graph.getAxis('left').setTextPen(pg.mkPen(color=axis_text_color))
        self.graph.getAxis('bottom').setTextPen(pg.mkPen(color=axis_text_color))
        
        self.graph.setYRange(40, 300, padding=0.1)  # type: ignore[call-arg]
        label_color = self.config['appearance']['colors']['graph']['axis_labels']
        if self.config['appearance'].get('show_y_label', True):
            self.graph.setLabel('left', 'Glucose', color=label_color, size='10pt')
        else:
            self.graph.setLabel('left', '')
        # Remove bottom label to avoid scientific notation display
        self.graph.setLabel('bottom', '', color=label_color, size='10pt')
        
        self.graph.sigRangeChanged.connect(self.on_range_changed)
        self._target_zone_items = []
        self.add_target_zones()
        self.current_time_line = pg.InfiniteLine(
            pos=datetime.now().timestamp(),
            angle=90,
            pen=pg.mkPen(color=self.config['appearance']['colors']['graph']['current_time_line'], width=1, cosmetic=True),
            movable=False
        )
        self.graph.addItem(self.current_time_line)
        self.current_time_line.setZValue(1000)

        self._line_items = []
        self._scatter_item = pg.ScatterPlotItem(symbol='o')
        self._scatter_item.setZValue(100)
        self.graph.addItem(self._scatter_item)

        self._iob_axis = pg.AxisItem('right')
        self._iob_axis.setPen(pg.mkPen(color=BOLUS_COLOR, width=1))
        self._iob_axis.setTextPen(pg.mkPen(color=BOLUS_COLOR))
        self._iob_axis.setLabel('IOB (U)', color=BOLUS_COLOR, size='10pt')
        self._iob_axis.setStyle(showValues=False, tickLength=0)
        if plot_item is not None:
            layout = getattr(plot_item, 'layout', None)
            if layout is not None:
                layout.addItem(self._iob_axis, 2, 2)

        self._iob_view = ViewBox()
        self._iob_view.setMouseEnabled(x=False, y=False)
        self._iob_view.setMenuEnabled(False)
        scene = self.graph.scene()
        if scene is not None:
            scene.addItem(self._iob_view)
        self._iob_axis.linkToView(self._iob_view)
        if plot_item is not None:
            self._iob_view.setXLink(plot_item)
            vb = getattr(plot_item, 'vb', None)
            if vb is not None:
                vb.sigResized.connect(self._update_iob_view_geometry)

        self._iob_zero_curve = pg.PlotCurveItem()
        self._iob_zero_curve.setPen(pg.mkPen(None))
        self._iob_curve = pg.PlotCurveItem()
        self._iob_curve.setPen(pg.mkPen(BOLUS_COLOR, width=2))
        self._iob_curve.setZValue(60)
        self._iob_fill_item = pg.FillBetweenItem(
            self._iob_zero_curve,
            self._iob_curve,
            brush=pg.mkBrush(30, 144, 255, 45),
        )
        self._iob_fill_item.setZValue(55)
        self._iob_value_text = pg.TextItem(color=BOLUS_COLOR, anchor=(1.0, 0.5))
        self._iob_value_text.setZValue(70)
        self._iob_view.addItem(self._iob_fill_item)
        self._iob_view.addItem(self._iob_zero_curve)
        self._iob_view.addItem(self._iob_curve)
        self._iob_view.addItem(self._iob_value_text)
        self._iob_axis.setVisible(False)
        self._iob_view.setVisible(False)
        self._update_iob_view_geometry()

        self._value_text_item = None
        self.treatment_items = []

        # Intercept mouse events on the graph so border-drag resize still works.
        # PlotWidget is a QGraphicsView; the actual mouse events go to the viewport child,
        # so we must install the filter on both.
        self.graph.installEventFilter(self)
        _vp = self.graph.viewport()
        if _vp is not None:
            _vp.setMouseTracking(True)
            _vp.installEventFilter(self)
        
        graph_container = QWidget()
        graph_container.setObjectName("GraphContainer")
        graph_layout = QVBoxLayout()
        graph_layout.setContentsMargins(1, 0, 1, 1)
        graph_layout.setSpacing(0)
        graph_layout.addWidget(self.graph)
        graph_container.setLayout(graph_layout)
        graph_container.setStyleSheet(
            "#GraphContainer { "
            "border-left: 1px solid rgba(255, 255, 255, 18); "
            "border-right: 1px solid rgba(255, 255, 255, 18); "
            "border-bottom: 1px solid rgba(255, 255, 255, 18); "
            "border-bottom-left-radius: 10px; "
            "border-bottom-right-radius: 10px; "
            "background: transparent; }"
        )
        self.main_layout.addWidget(graph_container, 1)

    def _update_iob_view_geometry(self):
        plot_item = self.graph.getPlotItem()
        if plot_item is None or not hasattr(self, '_iob_view'):
            return
        try:
            vb = getattr(plot_item, 'vb', None)
            if vb is None:
                return
            rect = vb.sceneBoundingRect()
            strip_height = max(34.0, rect.height() * 0.24)
            strip_rect = rect.adjusted(0, rect.height() - strip_height, 0, 0)
            self._iob_view.setGeometry(strip_rect)
            self._iob_view.linkedViewChanged(vb, self._iob_view.XAxis)
        except Exception:
            pass

    def add_target_zones(self):
        """Add colored zones for low, target, and high glucose ranges"""
        from PyQt6.QtGui import QColor

        if hasattr(self, '_target_zone_items'):
            for item in self._target_zone_items:
                try:
                    self.graph.removeItem(item)
                except Exception:
                    pass
            self._target_zone_items = []

        target_low = self.config['target_low']
        target_high = self.config['target_high']
        target_fill = self.config['appearance']['colors']['target_zones'].get('target_fill', '#00d4aa')
        target_fill_q = QColor(target_fill)

        target_zone = pg.LinearRegionItem(
            values=[target_low, target_high],
            brush=pg.mkBrush(target_fill_q.red(), target_fill_q.green(), target_fill_q.blue(), self.config['appearance']['target_zone_opacity']),
            pen=pg.mkPen(None),
            movable=False
        )
        self.graph.addItem(target_zone)
        self._target_zone_items.append(target_zone)
        
        low_zone = pg.LinearRegionItem(
            values=[40, target_low], 
            brush=pg.mkBrush(255, 0, 0, self.config['appearance']['target_zone_opacity']), 
            pen=pg.mkPen(None),
            movable=False
        )
        self.graph.addItem(low_zone)
        self._target_zone_items.append(low_zone)
        
        high_zone = pg.LinearRegionItem(
            values=[target_high, 300], 
            brush=pg.mkBrush(255, 165, 0, self.config['appearance']['target_zone_opacity']), 
            pen=pg.mkPen(None),
            movable=False
        )
        self.graph.addItem(high_zone)
        self._target_zone_items.append(high_zone)
        
        low_line_color = self.config['appearance']['colors']['target_zones']['low_line']
        high_line_color = self.config['appearance']['colors']['target_zones']['high_line']

        low_color = QColor(low_line_color)
        low_color.setAlpha(120)
        high_color = QColor(high_line_color)
        high_color.setAlpha(120)
        
        low_line = self.graph.addLine(y=target_low, pen=pg.mkPen(low_color, width=2, style=Qt.PenStyle.DashLine))
        high_line = self.graph.addLine(y=target_high, pen=pg.mkPen(high_color, width=2, style=Qt.PenStyle.DashLine))
        self._target_zone_items.append(low_line)
        self._target_zone_items.append(high_line)

    # ===== Dragging and Context Menu =====
    def eventFilter(self, obj, event):
        """Forward mouse events from child widgets (graph) to parent resize/drag logic
        when the cursor is inside the border resize zone."""
        from PyQt6.QtCore import QEvent
        _vp = self.graph.viewport()
        if obj in (self.graph, _vp) and _vp is not None and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        ):
            # Map the position from the source widget's local coords to this widget's local coords
            local_pos = self.mapFromGlobal(obj.mapToGlobal(event.position().toPoint()))  # type: ignore[union-attr]
            edge = self.get_resize_edge(local_pos)

            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.RightButton:
                    self.show_context_menu(event.globalPosition().toPoint())
                    return True
                if edge and event.button() == Qt.MouseButton.LeftButton:
                    self.resize_edge = edge
                    self.resize_start_pos = event.globalPosition().toPoint()
                    self.resize_start_geometry = self.geometry()
                    self.setCursor(self.get_resize_cursor(edge))
                    return True  # consume so graph doesn't start panning
            elif event.type() == QEvent.Type.MouseMove:
                if self.resize_edge and event.buttons() & Qt.MouseButton.LeftButton:
                    self.handle_resize(event.globalPosition().toPoint())
                    return True
                elif not (event.buttons() & Qt.MouseButton.LeftButton):
                    self._update_hover_cursor(local_pos)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if self.resize_edge and event.button() == Qt.MouseButton.LeftButton:
                    self.resize_edge = None
                    self.resize_start_pos = None
                    self.resize_start_geometry = None
                    self._update_hover_cursor(local_pos)
                    self.position_save_timer.start()
                    return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(event.globalPosition().toPoint())
        elif event.button() == Qt.MouseButton.LeftButton:
            self.resize_edge = self.get_resize_edge(event.position().toPoint())
            
            if self.resize_edge:
                self.resize_start_pos = event.globalPosition().toPoint()
                self.resize_start_geometry = self.geometry()
                self.setCursor(self.get_resize_cursor(self.resize_edge))
            else:
                self.old_pos = event.globalPosition().toPoint()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def show_context_menu(self, position):
        """Show context menu with close and resize options"""
        menu = QMenu(self)
        menu.setStyleSheet(CONTEXT_MENU_QSS)

        auto_resize_action = QAction("Enable Auto-resize" if self.user_resized else "Disable Auto-resize", self)
        auto_resize_action.triggered.connect(self.toggle_auto_resize)
        menu.addAction(auto_resize_action)

        add_treatment_action = QAction("Log Insulin / Carbs...", self)
        add_treatment_action.triggered.connect(self.show_treatment_dialog)
        menu.addAction(add_treatment_action)

        is_transparency_enabled = bool(
            self.config.get('appearance', {}).get('transparency_enabled', True)
        )
        graph_transparency_action = QAction(
            "Disable Transparency" if is_transparency_enabled else "Enable Transparency",
            self,
        )
        graph_transparency_action.triggered.connect(self.toggle_graph_transparency)
        menu.addAction(graph_transparency_action)
        
        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.show_settings_dialog)
        menu.addAction(settings_action)

        connection_action = QAction("Edit Connection...", self)
        connection_action.triggered.connect(self.show_connection_dialog)
        menu.addAction(connection_action)
        
        menu.addSeparator()
        
        close_action = QAction("Minimize to Tray", self)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)

        quit_action_ctx = QAction("Quit", self)
        quit_action_ctx.triggered.connect(self._quit_app)
        menu.addAction(quit_action_ctx)
        
        menu.exec(position)
    
    def toggle_auto_resize(self):
        """Toggle between auto-resize and manual resize modes"""
        self.user_resized = not self.user_resized
        
        if not self.user_resized and hasattr(self, 'label'):
            glucose_text = self.label.text()
            if glucose_text and glucose_text != "Loading...":
                self.auto_resize_to_fit_content(glucose_text)
        
        if hasattr(self, 'position_save_timer'):
            self.position_save_timer.start()

    def toggle_graph_transparency(self):
        """Enable/disable transparency effect for both graph and header from the opacity sliders."""
        appearance = dict(self.config.get('appearance', {}))
        appearance['transparency_enabled'] = not bool(
            appearance.get('transparency_enabled', True)
        )

        self.apply_settings({"appearance": appearance})
    
    def show_settings_dialog(self):
        dialog = SettingsDialog(self, self.config, DARK_QSS)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.update_glucose(fetch_remote=True)

    def show_connection_dialog(self):
        """Re-run the setup wizard to change Nightscout URL / API secret."""
        wizard = SetupWizard(CONFIG_FILE, DARK_QSS)
        wizard.url_input.setText(self.nightscout_url)
        wizard.secret_input.setText(self.api_secret_raw)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self.nightscout_url, self.api_secret, self.api_secret_raw, new_config = load_config()
            self.config.update(new_config)
            self.timezone_offset = self.config['timezone_offset']
            self._sync_iob_settings_from_nightscout()
            self.update_glucose()
    
    def apply_settings(self, new_config, fetch_remote=False):
        # Update connection credentials if they changed
        profile_sync_needed = False
        if new_config.get('nightscout_url'):
            if new_config['nightscout_url'] != self.nightscout_url:
                self._entries_cache = []       # new source → discard cached data
                self._treatments_cache = []
                profile_sync_needed = True
            self.nightscout_url = new_config['nightscout_url']
        if new_config.get('api_secret_raw'):
            if new_config['api_secret_raw'] != self.api_secret_raw:
                profile_sync_needed = True
            self.api_secret_raw = new_config['api_secret_raw']
            self.api_secret = hashlib.sha1(self.api_secret_raw.encode()).hexdigest()

        # If the desired history window grew, force a full re-fetch
        old_entries_to_fetch = self.config.get('entries_to_fetch', 90)
        new_entries_to_fetch = new_config.get('entries_to_fetch', old_entries_to_fetch)
        if new_entries_to_fetch > old_entries_to_fetch:
            self._entries_cache = []

        old_treatments_to_fetch = self.config.get('treatments_to_fetch', 50)
        new_treatments_to_fetch = new_config.get('treatments_to_fetch', old_treatments_to_fetch)
        if new_treatments_to_fetch > old_treatments_to_fetch:
            self._treatments_cache = []

        self.config.update(new_config)
        
        try:
            config_data = {
                "nightscout_url": self.nightscout_url,
                "api_secret": self.api_secret_raw,
                "refresh_interval_ms": self.config.get('refresh_interval', 10000),
                "timezone_offset_hours": self.config.get('timezone_offset', 0),
                "time_window_hours": self.config.get('time_window_hours', 1),
                "entries_to_fetch": self.config.get('entries_to_fetch', 90),
                "target_low": self.config.get('target_low', 70),
                "target_high": self.config.get('target_high', 180),
                "widget_width": self.config.get('widget_width', 400),
                "widget_height": self.config.get('widget_height', 280),
                "glucose_font_size": self.config.get('glucose_font_size', 18),
                "time_font_size": self.config.get('time_font_size', 12),
                "age_font_size": self.config.get('age_font_size', 10),
                "show_delta": self.config.get('show_delta', True),
                "show_float_glucose": self.config.get('show_float_glucose', True),
                "adaptive_dot_size": self.config.get('adaptive_dot_size', False),
                "data_point_size": self.config.get('data_point_size', 6),
                "gradient_interpolation": self.config.get('gradient_interpolation', True),
                "show_treatments": self.config.get('show_treatments', True),
                "treatments_to_fetch": self.config.get('treatments_to_fetch', 50),
                "default_insulin_type": self.config.get('default_insulin_type', 'Humalog Lispro'),
                "iob_dia_hours": self.config.get('iob_dia_hours', 5.0),
                "iob_peak_minutes": self.config.get('iob_peak_minutes', 75),
                "iob_onset_minutes": self.config.get('iob_onset_minutes', 15),
                "header_pills": self.config.get('header_pills', []),
                "appearance": self.config.get('appearance', {})
            }
            
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            log.error("Error saving config: %s", e)
        
        self._apply_header_label_styles()
        self._apply_close_button_style(False)
        self._apply_dynamic_header_fonts()

        self._update_graph_background()
        self._apply_header_background()
        self.add_target_zones()
        self._apply_widget_background()
        self._last_treatment_render_key = None

        if profile_sync_needed:
            self._sync_iob_settings_from_nightscout()

        self.update_glucose(fetch_remote=fetch_remote)

    def mouseMoveEvent(self, event):
        if self.resize_edge and event.buttons() & Qt.MouseButton.LeftButton:
            self.handle_resize(event.globalPosition().toPoint())
        elif self.old_pos and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.old_pos
            new_x = self.x() + delta.x()
            new_y = self.y() + delta.y()
            
            constrained_pos = self.constrain_to_screen_bounds(new_x, new_y)
            self.move(constrained_pos)
            
            self.old_pos = event.globalPosition().toPoint()
            
            self.position_save_timer.start()
        else:
            self._update_hover_cursor(event.position().toPoint())
                
    def mouseReleaseEvent(self, event):
        """Handle mouse release events"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.resize_edge = None
            self.resize_start_pos = None
            self.resize_start_geometry = None
            self.old_pos = None
            self._update_hover_cursor(event.position().toPoint())
            
            if hasattr(self, 'position_save_timer'):
                self.position_save_timer.start()
        
    def moveEvent(self, event):
        """Called when window is moved, schedule position saving"""
        super().moveEvent(event)
        if hasattr(self, 'position_save_timer'):
            self.position_save_timer.start()
            
    def get_resize_edge(self, pos):
        """Determine which edge of the window is near the mouse position"""
        edge_margin = self.resize_edge_margin
        rect = self.rect()
        
        if (pos.x() <= edge_margin and pos.y() <= edge_margin):
            return 'top-left'
        elif (pos.x() >= rect.width() - edge_margin and pos.y() <= edge_margin):
            return 'top-right'
        elif (pos.x() <= edge_margin and pos.y() >= rect.height() - edge_margin):
            return 'bottom-left'
        elif (pos.x() >= rect.width() - edge_margin and pos.y() >= rect.height() - edge_margin):
            return 'bottom-right'
        elif pos.y() <= edge_margin:
            return 'top'
        elif pos.y() >= rect.height() - edge_margin:
            return 'bottom'
        elif pos.x() <= edge_margin:
            return 'left'
        elif pos.x() >= rect.width() - edge_margin:
            return 'right'
        
        return None
    
    def get_resize_cursor(self, edge):
        """Get the appropriate cursor for the resize edge"""
        cursors = {
            'top': Qt.CursorShape.SizeVerCursor,
            'bottom': Qt.CursorShape.SizeVerCursor,
            'left': Qt.CursorShape.SizeHorCursor,
            'right': Qt.CursorShape.SizeHorCursor,
            'top-left': Qt.CursorShape.SizeFDiagCursor,
            'top-right': Qt.CursorShape.SizeBDiagCursor,
            'bottom-left': Qt.CursorShape.SizeBDiagCursor,
            'bottom-right': Qt.CursorShape.SizeFDiagCursor
        }
        return cursors.get(edge, Qt.CursorShape.ArrowCursor)
    
    def handle_resize(self, global_pos):
        """Handle window resizing based on which edge is being dragged"""
        if self.resize_start_pos is None or self.resize_start_geometry is None or self.resize_edge is None:
            return
            
        delta = global_pos - self.resize_start_pos
        new_rect = QRect(self.resize_start_geometry)
        
        if 'right' in self.resize_edge:
            new_rect.setWidth(max(self.minimumWidth(), self.resize_start_geometry.width() + delta.x()))
        elif 'left' in self.resize_edge:
            new_width = max(self.minimumWidth(), self.resize_start_geometry.width() - delta.x())
            width_diff = new_width - new_rect.width()
            new_rect.setX(new_rect.x() - width_diff)
            new_rect.setWidth(new_width)
            
        if 'bottom' in self.resize_edge:
            new_rect.setHeight(max(self.minimumHeight(), self.resize_start_geometry.height() + delta.y()))
        elif 'top' in self.resize_edge:
            new_height = max(self.minimumHeight(), self.resize_start_geometry.height() - delta.y())
            height_diff = new_height - new_rect.height()
            new_rect.setY(new_rect.y() - height_diff)
            new_rect.setHeight(new_height)
        
        new_rect.setWidth(min(self.maximumWidth(), max(self.minimumWidth(), new_rect.width())))
        new_rect.setHeight(min(self.maximumHeight(), max(self.minimumHeight(), new_rect.height())))
        
        self.setGeometry(new_rect)

    def closeEvent(self, event):
        self.save_position_and_size()
        self.save_zoom_state()
        if hasattr(self, '_tray') and self._tray.isVisible():
            self.hide()
            if hasattr(self, '_show_hide_action'):
                self._show_hide_action.setText("Show NSOverlay")
            event.ignore()
        else:
            self._stop_fetch_thread()
            self._stop_treatment_write_threads()
            event.accept()

    # ===== System Tray =====

    def _setup_tray(self):
        """Create and configure the system tray icon."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self._tray = QSystemTrayIcon(self)

        # Use the app icon as initial tray icon, it will be replaced after first glucose fetch
        _icon_path = os.path.join(_BASE_DIR, "icon.ico")
        if os.path.exists(_icon_path):
            self._tray.setIcon(QIcon(_icon_path))
        else:
            # Fallback: draw a plain placeholder
            self._tray.setIcon(self._make_tray_icon("--", "#444444"))

        self._tray.setToolTip("NSOverlay — loading...")

        tray_menu = QMenu()
        tray_menu.setStyleSheet(CONTEXT_MENU_QSS)

        self._show_hide_action = QAction("Hide NSOverlay", self)
        self._show_hide_action.triggered.connect(self._toggle_visibility)
        tray_menu.addAction(self._show_hide_action)

        tray_menu.addSeparator()

        add_treatment_action = QAction("Log Insulin / Carbs...", self)
        add_treatment_action.triggered.connect(self.show_treatment_dialog)
        tray_menu.addAction(add_treatment_action)

        tray_menu.addSeparator()

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.show_settings_dialog)
        tray_menu.addAction(settings_action)

        tray_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _make_tray_icon(self, glucose, bg_hex):
        """Render a 64x64 QIcon with the glucose value on a colour-coded background."""
        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Coloured rounded-rectangle background
        bg = QColor(bg_hex)
        bg.setAlpha(230)
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, size - 4, size - 4, 10, 10)

        # Glucose text — pick dark or light colour based on background luminance
        text = str(glucose)
        font_size = 32 if len(text) <= 2 else 26
        font = QFont("sans-serif", font_size, QFont.Weight.Bold)
        painter.setFont(font)
        luma = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
        text_color = QColor("#000000") if luma > 128 else QColor("#ffffff")
        painter.setPen(text_color)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)

        painter.end()
        return QIcon(pixmap)

    def _normalize_qt_color_key(self, color):
        qcolor = QColor(color)
        if qcolor.isValid():
            return qcolor.name(QColor.NameFormat.HexArgb)
        return str(color)

    def _get_cached_brush(self, color):
        key = self._normalize_qt_color_key(color)
        brush = self._brush_cache.get(key)
        if brush is None:
            brush = pg.mkBrush(QColor(color))
            self._brush_cache[key] = brush
        return brush

    def _get_cached_pen(self, color, width=1, style=Qt.PenStyle.SolidLine, cosmetic=False, round_join=False):
        style_key = getattr(style, 'value', style)
        key = (
            self._normalize_qt_color_key(color),
            float(width),
            style_key,
            bool(cosmetic),
            bool(round_join),
        )
        pen = self._pen_cache.get(key)
        if pen is None:
            pen = pg.mkPen(color=QColor(color), width=width, style=style, cosmetic=cosmetic)
            if round_join:
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            self._pen_cache[key] = pen
        return pen

    def _update_tray_icon(self, glucose, trend_arrow, delta_text):
        """Update tray icon colour and tooltip to reflect the latest glucose reading."""
        if not hasattr(self, '_tray'):
            return

        target_low = self.config['target_low']
        target_high = self.config['target_high']
        glucose_colors = self.config['appearance']['colors']['glucose_ranges']

        # Check data staleness — mirror the age-label thresholds (>15 min = stale)
        is_stale = False
        if hasattr(self, 'last_entry_time') and self.last_entry_time:
            age_seconds = int((datetime.utcnow() - self.last_entry_time).total_seconds())
            is_stale = age_seconds > 900  # 15 minutes

        if is_stale:
            bg_hex = "#666666"  # Grey — stale data
        elif glucose < target_low:
            bg_hex = glucose_colors['low']
        elif glucose <= target_high:
            bg_hex = glucose_colors['in_range']
        else:
            bg_hex = glucose_colors['high']

        icon_key = (str(glucose), bg_hex)
        if icon_key != self._last_tray_icon_key or self._last_tray_icon is None:
            self._last_tray_icon = self._make_tray_icon(glucose, bg_hex)
            self._last_tray_icon_key = icon_key

        self._tray.setIcon(self._last_tray_icon)

        # Cache so update_time_display can re-render on stale transition
        self._last_tray_glucose = glucose
        self._last_tray_trend = trend_arrow
        self._last_tray_delta = delta_text
        self._tray_was_stale = is_stale

        self._refresh_tray_tooltip(glucose, trend_arrow, delta_text, is_stale)

    def _refresh_tray_tooltip(self, glucose, trend_arrow, delta_text, is_stale):
        """Refresh tray tooltip text using latest glucose, age and header pill values."""
        if not hasattr(self, '_tray'):
            return

        age_text = self.age_label.text()
        tooltip = f"{glucose} {trend_arrow}"
        if delta_text:
            tooltip += f" {delta_text.strip()}"
        if age_text:
            stale_prefix = "⚠ STALE — " if is_stale else ""
            tooltip += f"\n{stale_prefix}{age_text}"
        if self._tray_pill_texts:
            tooltip += "\n" + "\n".join(self._tray_pill_texts)
        self._tray.setToolTip(f"NSOverlay\n{tooltip}")

    def _build_header_pill_render_key(self, treatments):
        pill_configs = self.config.get('header_pills', [])
        if not pill_configs:
            return ('no-pills', self.width(), self.timezone_offset)

        def _event_keys_for_pill(pill_cfg):
            event_types = pill_cfg.get('event_types')
            keys = []

            if isinstance(event_types, list):
                for event_type in event_types:
                    if isinstance(event_type, str):
                        normalized = event_type.strip().lower()
                        if normalized and normalized not in keys:
                            keys.append(normalized)

            if not keys:
                event_type_cfg = pill_cfg.get('event_type', '')
                if isinstance(event_type_cfg, str):
                    separators = ['|', ',']
                    expanded = [event_type_cfg]
                    for sep in separators:
                        if any(sep in part for part in expanded):
                            next_parts = []
                            for part in expanded:
                                next_parts.extend(part.split(sep))
                            expanded = next_parts
                    for raw_key in expanded:
                        normalized = raw_key.strip().lower()
                        if normalized and normalized not in keys:
                            keys.append(normalized)

            return tuple(keys)

        normalized_pills = []
        relevant_event_types = set()
        for pill_cfg in pill_configs:
            if not bool(pill_cfg.get('enabled', True)):
                continue

            event_keys = _event_keys_for_pill(pill_cfg)
            if not event_keys:
                continue

            show_field = pill_cfg.get('show_field')
            show_fields = pill_cfg.get('show_fields', [])
            if show_fields and isinstance(show_fields, list):
                fields_list = tuple(str(field) for field in show_fields)
            elif show_field:
                if isinstance(show_field, list):
                    fields_list = tuple(str(field) for field in show_field)
                else:
                    fields_list = (str(show_field),)
            else:
                fields_list = ()

            suffix_map = pill_cfg.get('suffix_map', {})
            if isinstance(suffix_map, dict):
                suffix_map_items = tuple(sorted((str(k), str(v)) for k, v in suffix_map.items()))
            else:
                suffix_map_items = ()

            normalized_pills.append((
                event_keys,
                fields_list,
                bool(pill_cfg.get('sum_daily', False)),
                float(pill_cfg.get('max_age_hours', 24)),
                str(pill_cfg.get('label', '')),
                str(pill_cfg.get('suffix', '')),
                suffix_map_items,
                bool(pill_cfg.get('bold', False)),
            ))
            relevant_event_types.update(event_keys)

        treatment_signature = []
        if relevant_event_types:
            for treatment in treatments:
                if not isinstance(treatment, dict):
                    continue
                event_type = treatment.get('eventType', '').lower()
                if event_type in relevant_event_types:
                    treatment_signature.append((
                        treatment.get('_id'),
                        treatment.get('created_at'),
                        event_type,
                    ))

        return (
            self.width(),
            self.timezone_offset,
            tuple(normalized_pills),
            tuple(treatment_signature),
        )

    def _toggle_visibility(self):
        """Show or hide the main widget and update the tray menu label."""
        if self.isVisible():
            self.hide()
            if hasattr(self, '_show_hide_action'):
                self._show_hide_action.setText("Show NSOverlay")
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            if hasattr(self, '_show_hide_action'):
                self._show_hide_action.setText("Hide NSOverlay")

    def _on_tray_activated(self, reason):
        """Toggle visibility on double-click of the tray icon."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_visibility()

    def _quit_app(self):
        """Fully quit the application from the tray menu."""
        if hasattr(self, '_tray'):
            self._tray.hide()
        self.save_position_and_size()
        self.save_zoom_state()
        self._stop_fetch_thread()
        self._stop_treatment_write_threads()
        QApplication.quit()

    def show_treatment_dialog(self):
        """Open a dialog for logging insulin and carbs to Nightscout."""
        dialog = TreatmentDialog(
            self,
            DARK_QSS,
            default_insulin_type=str(self.config.get('default_insulin_type', 'Humalog Lispro')),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        request = dialog.value()
        request = TreatmentWriteRequest(
            nightscout_url=self.nightscout_url,
            api_secret=self.api_secret,
            event_type=request.event_type,
            insulin=request.insulin,
            insulin_type=request.insulin_type,
            carbs=request.carbs,
            notes=request.notes,
            entered_by=request.entered_by,
        )
        self._submit_treatment_write(request)

    def _submit_treatment_write(self, request: TreatmentWriteRequest):
        thread = NightscoutTreatmentWriteThread(request, self)
        self._pending_treatment_threads.add(thread)
        thread.submitted.connect(self._on_treatment_write_success)
        thread.failed.connect(self._on_treatment_write_error)
        thread.finished.connect(lambda thread=thread: self._pending_treatment_threads.discard(thread))
        thread.start()

    def _show_treatment_write_message(self, title: str, message: str, is_error: bool = False):
        icon = QSystemTrayIcon.MessageIcon.Critical if is_error else QSystemTrayIcon.MessageIcon.Information
        if hasattr(self, '_tray'):
            self._tray.showMessage(title, message, icon, 4000)
            return

        box_icon = QMessageBox.Icon.Critical if is_error else QMessageBox.Icon.Information
        QMessageBox(box_icon, title, message, QMessageBox.StandardButton.Ok, self).exec()

    def _on_treatment_write_success(self, response):
        self.update_glucose(fetch_remote=True)
        message = "Treatment saved to Nightscout."
        if isinstance(response, dict):
            candidate = response.get("message") or response.get("result")
            if isinstance(candidate, str) and candidate.strip():
                message = candidate.strip()
        self._show_treatment_write_message("Nightscout", message)

    def _on_treatment_write_error(self, error_text):
        log.warning("Nightscout treatment write failed: %s", error_text)
        self._show_treatment_write_message("Nightscout", f"Could not save treatment: {error_text}", is_error=True)

    # ===== Position & Size Memory =====
    def save_position_and_size(self):
        """Save current window position and size with error handling"""
        try:
            data = {
                "x": self.x(), 
                "y": self.y(),
                "width": self.width(),
                "height": self.height(),
                "user_resized": getattr(self, 'user_resized', False)
            }
            with open(POSITION_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except (IOError, OSError) as e:
            log.warning("Could not save position and size: %s", e)
    
    def validate_position_on_screen_change(self):
        """Validate and adjust position when screen configuration changes"""
        current_pos = self.pos()
        constrained_pos = self.constrain_to_screen_bounds(current_pos.x(), current_pos.y())
        
        if current_pos != constrained_pos:
            self.move(constrained_pos)
            self.save_position_and_size()

    def get_all_screen_geometries(self):
        """Get combined geometry of all available screens"""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return []
        screens = app.screens()
        
        if not screens:
            # Fallback to primary screen if no screens found
            primary = app.primaryScreen()
            return [primary.availableGeometry()] if primary else []
            
        return [screen.availableGeometry() for screen in screens]
    
    def constrain_to_screen_bounds(self, x, y):
        """Constrain window position to stay within available screen bounds"""
        widget_width = self.width()
        widget_height = self.height()
        
        screen_geometries = self.get_all_screen_geometries()
        
        best_screen = None
        min_distance = float('inf')
        
        for screen_rect in screen_geometries:
            window_center_x = x + widget_width // 2
            window_center_y = y + widget_height // 2
            screen_center_x = screen_rect.x() + screen_rect.width() // 2
            screen_center_y = screen_rect.y() + screen_rect.height() // 2
            
            distance = ((window_center_x - screen_center_x) ** 2 + 
                       (window_center_y - screen_center_y) ** 2) ** 0.5
            
            if distance < min_distance:
                min_distance = distance
                best_screen = screen_rect
        
        if best_screen is None:
            best_screen = screen_geometries[0]
        
        margin = 30
        
        min_x = best_screen.x() - widget_width + margin
        max_x = best_screen.x() + best_screen.width() - margin
        constrained_x = max(min_x, min(x, max_x))
        
        min_y = best_screen.y() - widget_height + margin
        max_y = best_screen.y() + best_screen.height() - margin
        constrained_y = max(min_y, min(y, max_y))
        
        return QPoint(constrained_x, constrained_y)
    
    def load_position(self):
        if os.path.exists(POSITION_FILE):
            try:
                with open(POSITION_FILE, "r") as f:
                    data = json.load(f)
                
                # Handle both old format (position only) and new format (position + size)
                if isinstance(data, dict):
                    x = data.get("x", 100)
                    y = data.get("y", 100)
                    width = data.get("width", self.base_width)
                    height = data.get("height", self.base_height)
                    self.user_resized = data.get("user_resized", False)
                else:
                    # Old format compatibility
                    x, y = data.get("x", 100), data.get("y", 100)
                    width, height = self.base_width, self.base_height
                    self.user_resized = False
                
                width = max(self.minimumWidth(), min(self.maximumWidth(), width))
                height = max(self.minimumHeight(), min(self.maximumHeight(), height))
                
                self.resize(width, height)
                
                constrained_pos = self.constrain_to_screen_bounds(x, y)
                self.move(constrained_pos)
                
            except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
                log.error("Error loading position/size: %s", e)
                try:
                    os.remove(POSITION_FILE)
                except:
                    pass
                self.center_on_screen()
        else:
            self.center_on_screen()
    
    def center_on_screen(self):
        """Center the widget on the primary screen"""
        primary = QApplication.primaryScreen()
        if primary is None:
            return
        screen = primary.availableGeometry()
        widget_width = self.width()
        widget_height = self.height()
        
        center_x = screen.x() + (screen.width() - widget_width) // 2
        center_y = screen.y() + (screen.height() - widget_height) // 2
        
        self.move(QPoint(center_x, center_y))
        
    def auto_resize_to_fit_content(self, glucose_text):
        """Automatically resize window to fit glucose content optimally (only if not manually resized)"""
        if getattr(self, 'user_resized', False):
            return
            
        try:
            font = QFont("sans-serif", self.config['glucose_font_size'], QFont.Weight.Bold)
            font_metrics = QFontMetrics(font)
            text_width = font_metrics.horizontalAdvance(glucose_text)
            padding = 40
            
            time_font = QFont("sans-serif", self.config['time_font_size'], QFont.Weight.Medium)
            time_metrics = QFontMetrics(time_font)
            time_width = time_metrics.horizontalAdvance("00:00 00:00")
            
            content_width = max(text_width, time_width) + padding
            min_width = 200
            max_width = 600
            
            optimal_width = max(min_width, min(content_width, max_width))
            
            current_height = self.height()
            optimal_height = max(self.base_height, current_height)
            
            current_width = self.width()
            width_diff = abs(current_width - optimal_width)
            
            if width_diff > 10:
                old_pos = self.pos()
                
                self.resize(optimal_width, optimal_height)
                
                width_change = optimal_width - current_width
                new_x = old_pos.x() - width_change // 2
                new_y = old_pos.y()
                
                constrained_pos = self.constrain_to_screen_bounds(new_x, new_y)
                self.move(constrained_pos)
                
                if hasattr(self, 'position_save_timer'):
                    self.position_save_timer.start()
                
        except Exception as e:
            log.warning("Auto-resize failed: %s", e)
    
    def save_zoom_state(self):
        """Save current Y-axis zoom state"""
        try:
            y_range = self.graph.viewRange()[1]
            zoom_state = {"y_min": y_range[0], "y_max": y_range[1]}
            with open(ZOOM_FILE, "w") as f:
                json.dump(zoom_state, f)
        except:
            pass
    
    def load_zoom_state(self):
        """Load and apply saved Y-axis zoom state"""
        try:
            if os.path.exists(ZOOM_FILE):
                with open(ZOOM_FILE, "r") as f:
                    zoom_state = json.load(f)
                    self.graph.setYRange(zoom_state["y_min"], zoom_state["y_max"], padding=0)  # type: ignore[call-arg]
        except:
            y_min, y_max = self.calculate_adaptive_y_range()
            self.graph.setYRange(y_min, y_max, padding=0.05)  # type: ignore[call-arg]

    # ===== Real-time Settings Methods =====
    def setup_shortcuts(self):
        """Setup keyboard shortcuts for real-time settings changes"""
        from PyQt6.QtGui import QShortcut, QKeySequence
        
        gradient_shortcut = QShortcut(QKeySequence('Ctrl+G'), self)
        gradient_shortcut.activated.connect(self.toggle_gradient_interpolation)
        
        refresh_shortcut = QShortcut(QKeySequence('Ctrl+R'), self)
        refresh_shortcut.activated.connect(self.reload_config)
    
    def toggle_gradient_interpolation(self):
        """Toggle gradient interpolation on/off in real-time"""
        from PyQt6.QtCore import QTimer
        
        current_state = self.config.get('gradient_interpolation', False)
        self.config['gradient_interpolation'] = not current_state
        
        self.save_config_setting('gradient_interpolation', self.config['gradient_interpolation'])
        self.update_glucose()
        
        status = "ON" if self.config['gradient_interpolation'] else "OFF"
        log.info("Gradient interpolation: %s", status)
        
        original_title = self.windowTitle()
        self.setWindowTitle(f"Gradient Interpolation: {status}")
        QTimer.singleShot(2000, lambda: self.setWindowTitle(original_title))
    
    def reload_config(self):
        """Reload configuration from file in real-time"""
        try:
            old_config = self.config.copy()
            old_url = self.nightscout_url
            self.nightscout_url, self.api_secret, self.api_secret_raw, new_config = load_config()
            self.config = new_config
            self.timezone_offset = self.config['timezone_offset']
            
            # Discard cache if the server URL changed or the history window grew
            if (self.nightscout_url != old_url or
                    self.config.get('entries_to_fetch', 90) > old_config.get('entries_to_fetch', 90)):
                self._entries_cache = []
            if (self.nightscout_url != old_url or
                    self.config.get('treatments_to_fetch', 50) > old_config.get('treatments_to_fetch', 50)):
                self._treatments_cache = []

            if old_config['refresh_interval'] != self.config['refresh_interval']:
                self.timer.setInterval(self.config['refresh_interval'])
            
            self.update_glucose()
            log.info("Configuration reloaded successfully")
        except Exception as e:
            log.error("Error reloading config: %s", e)
    
    def save_config_setting(self, key, value):
        """Save a single setting to the config file"""
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            config_data[key] = value
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            log.error("Error saving config setting '%s': %s", key, e)

    # ===== Helper Methods =====
    def interpolate_color(self, base_color, target_color, factor):
        """Interpolate between two colors based on a factor (0-1)"""
        base = QColor(base_color)
        target = QColor(target_color)
        
        r = int(base.red() + (target.red() - base.red()) * factor)
        g = int(base.green() + (target.green() - base.green()) * factor)
        b = int(base.blue() + (target.blue() - base.blue()) * factor)
        
        return QColor(r, g, b).name()
    
    def get_glucose_color_with_interpolation(self, sgv, target_low, target_high, glucose_colors,
                                                 sgv_max=None, sgv_min=None):
        """Get glucose color with optional gradient interpolation.

        When gradient_interpolation is enabled:
        - HIGH: first point above target_high = yellow, actual peak (sgv_max) = red
        - LOW:  first point below target_low  = yellow, actual trough (sgv_min) = red
        sgv_max / sgv_min are the actual extreme values in the current dataset.
        """
        if not self.config.get('gradient_interpolation', False):
            if sgv < target_low:
                return glucose_colors['low']
            elif target_low <= sgv <= target_high:
                return glucose_colors['in_range']
            else:
                return glucose_colors['high']
        
        if sgv < target_low:
            if sgv_min is not None and sgv_min < target_low:
                full_range = target_low - sgv_min
            else:
                full_range = max(target_low - 40, 30)
            distance_below = target_low - sgv
            factor = min(distance_below / full_range, 1.0) if full_range > 0 else 1.0
            return self.interpolate_color('#FFFF00', '#FF0000', factor)
        
        elif sgv > target_high:
            if sgv_max is not None and sgv_max > target_high:
                full_range = sgv_max - target_high
            else:
                full_range = 50
            distance_above = sgv - target_high
            factor = min(distance_above / full_range, 1.0) if full_range > 0 else 1.0
            return self.interpolate_color('#FFFF00', '#FF0000', factor)
        
        else:
            return glucose_colors['in_range']
    
    # ===== Data Fetch =====
    def clear_treatments(self):
        """Remove all treatment markers from the graph"""
        for item in self.treatment_items:
            self.graph.removeItem(item)
        self.treatment_items = []

    def _get_insulin_type_for_treatment(self, treatment):
        insulin_type = _normalize_insulin_type_name(treatment.get('insulinType'))
        if insulin_type == 'Humalog Lispro':
            insulin_type = _normalize_insulin_type_name(treatment.get('insulin_type'))
        if insulin_type == 'Humalog Lispro':
            insulin_type = _normalize_insulin_type_name(
                self._profile_iob_settings.get('default_insulin_type', 'Humalog Lispro')
            )
        if insulin_type == 'Humalog Lispro':
            insulin_type = _normalize_insulin_type_name(self.config.get('default_insulin_type', 'Humalog Lispro'))
        return insulin_type

    def _get_insulin_model_params(self, insulin_type):
        normalized = _normalize_insulin_type_name(insulin_type)
        base_dia_minutes = float(self._profile_iob_settings.get('iob_dia_hours', self.config.get('iob_dia_hours', 5.0))) * 60.0
        base_peak_minutes = float(self._profile_iob_settings.get('iob_peak_minutes', self.config.get('iob_peak_minutes', 75)))
        base_onset_minutes = float(self._profile_iob_settings.get('iob_onset_minutes', self.config.get('iob_onset_minutes', 15)))
        factors = INSULIN_TYPE_MODEL_FACTORS.get(normalized, INSULIN_TYPE_MODEL_FACTORS['Humalog Lispro'])

        dia_minutes = max(60.0, base_dia_minutes * float(factors.get('dia_scale', 1.0)))
        peak_minutes = max(10.0, base_peak_minutes * float(factors.get('peak_scale', 1.0)))
        onset_minutes = max(0.0, base_onset_minutes * float(factors.get('onset_scale', 1.0)))
        onset_minutes = min(onset_minutes, dia_minutes - 10.0)
        peak_minutes = min(peak_minutes, dia_minutes - 5.0)
        return dia_minutes, peak_minutes, onset_minutes

    def _calculate_remaining_fraction(self, elapsed_minutes, dia_minutes, peak_minutes):
        if dia_minutes <= 0:
            return 0.0

        if elapsed_minutes <= 0:
            return 1.0
        if elapsed_minutes >= dia_minutes:
            return 0.0
        if peak_minutes <= 0:
            peak_minutes = dia_minutes * 0.3
        if peak_minutes >= dia_minutes:
            peak_minutes = dia_minutes - 1.0

        # Nightscout-style user parameters: DIA and peak define the activity curve.
        if elapsed_minutes <= peak_minutes:
            completed_fraction = (elapsed_minutes * elapsed_minutes) / (peak_minutes * dia_minutes)
            return max(0.0, min(1.0, 1.0 - completed_fraction))

        descent_den = dia_minutes - peak_minutes
        if descent_den <= 0:
            return 0.0
        activity_max = 2.0 / dia_minutes
        tail_area = (activity_max / descent_den) * (
            dia_minutes * (elapsed_minutes - peak_minutes)
            - ((elapsed_minutes * elapsed_minutes) - (peak_minutes * peak_minutes)) / 2.0
        )
        completed_fraction = (peak_minutes / dia_minutes) + tail_area
        return max(0.0, min(1.0, 1.0 - completed_fraction))

    def _calculate_iob_value(self, sample_timestamp, treatments):
        iob_units = 0.0
        for treatment in treatments:
            if not isinstance(treatment, dict):
                continue

            insulin_amount = treatment.get('insulin', 0)
            try:
                insulin_amount = float(insulin_amount)
            except (TypeError, ValueError):
                continue

            if insulin_amount <= 0:
                continue

            created_at = treatment.get('created_at', treatment.get('timestamp'))
            if not created_at:
                continue

            created_utc = self._parse_ns_datetime(created_at)
            if created_utc is None:
                continue

            treatment_local = created_utc + timedelta(hours=self.timezone_offset)
            elapsed_minutes = (sample_timestamp - treatment_local.timestamp()) / 60.0
            if elapsed_minutes < 0:
                continue

            insulin_type = self._get_insulin_type_for_treatment(treatment)
            dia_minutes, peak_minutes, onset_minutes = self._get_insulin_model_params(insulin_type)
            if elapsed_minutes <= onset_minutes:
                fraction_remaining = 1.0
            else:
                effective_elapsed = elapsed_minutes - onset_minutes
                effective_dia = max(30.0, dia_minutes - onset_minutes)
                effective_peak = max(5.0, peak_minutes - onset_minutes)
                effective_peak = min(effective_peak, effective_dia - 1.0)
                fraction_remaining = self._calculate_remaining_fraction(
                    effective_elapsed,
                    effective_dia,
                    effective_peak,
                )
            iob_units += insulin_amount * fraction_remaining

        return iob_units

    def _build_iob_series(self, treatments):
        if not treatments:
            return []

        current_now = datetime.now().timestamp()
        try:
            x_min, x_max = self.graph.viewRange()[0]
        except Exception:
            time_window_seconds = self.config.get('time_window_hours', 3) * 60 * 60
            x_min = current_now - time_window_seconds
            x_max = current_now

        sample_start = float(x_min)
        sample_end = min(float(x_max), current_now)
        if sample_end <= sample_start:
            return []

        sample_step_seconds = max(60, int(max(sample_end - sample_start, 60) / 120))
        x_values = []
        y_values = []

        sample_time = sample_start
        while sample_time < sample_end:
            x_values.append(sample_time)
            y_values.append(self._calculate_iob_value(sample_time, treatments))
            sample_time += sample_step_seconds

        x_values.append(sample_end)
        y_values.append(self._calculate_iob_value(sample_end, treatments))
        return list(zip(x_values, y_values))

    def _update_iob_overlay(self):
        if not hasattr(self, '_iob_curve') or not hasattr(self, '_iob_view'):
            return

        series = self._build_iob_series(self._treatments_cache)
        if not series:
            self._iob_curve.setData([], [])
            self._iob_zero_curve.setData([], [])
            if hasattr(self, '_iob_value_text'):
                self._iob_value_text.setText("")
            self._iob_view.setVisible(False)
            return

        x_values = [point[0] for point in series]
        y_values = [point[1] for point in series]
        max_iob = max(y_values) if y_values else 0.0
        current_iob = y_values[-1] if y_values else 0.0

        if max_iob <= 0.01 or current_iob <= 0.01:
            self._iob_curve.setData([], [])
            self._iob_zero_curve.setData([], [])
            if hasattr(self, '_iob_value_text'):
                self._iob_value_text.setText("")
            self._iob_view.setVisible(False)
            return

        self._iob_zero_curve.setData(x_values, [0.0] * len(x_values), antialias=True)
        self._iob_curve.setData(x_values, y_values, antialias=True)

        self._iob_view.setVisible(True)
        self._iob_view.setYRange(0, max(0.5, max_iob * 1.15), padding=0)
        self._update_iob_view_geometry()

        if hasattr(self, '_iob_value_text'):
            iob_text = f"IOB {current_iob:.1f}U"
            try:
                x_max = float(self.graph.viewRange()[0][1])
            except Exception:
                x_max = x_values[-1]
            right_padding = 20
            label_x = max(x_values[0], x_max - right_padding)
            y_top = max(0.5, max_iob * 1.15)
            label_y = y_top * 0.72
            self._iob_value_text.setText(iob_text)
            self._iob_value_text.setPos(label_x, label_y)

    def _ensure_line_item(self, index):
        while len(self._line_items) <= index:
            curve = pg.PlotCurveItem()
            curve.setZValue(20)
            self.graph.addItem(curve)
            self._line_items.append(curve)
        return self._line_items[index]

    def _apply_line_segments(self, segments, width, style):
        for idx, (xs, ys, color) in enumerate(segments):
            curve = self._ensure_line_item(idx)
            curve.setData(xs, ys, antialias=True)
            curve.setPen(self._get_cached_pen(color, width=width, style=style, round_join=True))

        for idx in range(len(segments), len(self._line_items)):
            self._line_items[idx].setData([], [])

    def _build_render_key(self):
        latest_entry = self._entries_cache[-1] if self._entries_cache else {}
        latest_treatment = self._treatments_cache[-1] if self._treatments_cache else {}
        appearance = self.config.get('appearance', {})
        appearance_graph = appearance.get('colors', {}).get('graph', {})

        return (
            latest_entry.get('_id'),
            latest_entry.get('date'),
            latest_entry.get('sgv'),
            len(self._entries_cache),
            latest_treatment.get('_id'),
            len(self._treatments_cache),
            self.config.get('timezone_offset'),
            self.config.get('time_window_hours'),
            self.config.get('show_treatments', True),
            self.config.get('show_float_glucose', True),
            self.config.get('show_delta', True),
            self.config.get('gradient_interpolation', True),
            self.config.get('target_low'),
            self.config.get('target_high'),
            self.config.get('data_point_size'),
            self.config.get('adaptive_dot_size', False),
            appearance.get('graph_line_style', 'solid'),
            appearance.get('graph_line_width', 2),
            appearance.get('graph_line_smooth', False),
            appearance_graph.get('main_line', '#a0a0a0'),
            appearance.get('marker_outline_color', '#000000'),
            appearance.get('marker_outline_width', 1.5),
            str(self.config.get('header_pills', [])),
        )

    def _build_treatment_render_key(self, treatments, y_min, y_max):
        if not treatments:
            return (None, round(y_min, 2), round(y_max, 2), self.timezone_offset)

        relevant = []
        graph_event_types = {
            'correction bolus', 'meal bolus', 'bolus',
            'carb correction', 'carbs', 'exercise', 'basal injection'
        }
        for treatment in treatments:
            event_type = treatment.get('eventType', '').lower()
            if event_type in graph_event_types:
                relevant.append((
                    treatment.get('_id'),
                    treatment.get('created_at', treatment.get('timestamp')),
                    event_type,
                    treatment.get('insulin'),
                    treatment.get('carbs'),
                    treatment.get('duration'),
                    treatment.get('notes'),
                ))

        return (
            tuple(relevant),
            round(y_min, 2),
            round(y_max, 2),
            self.timezone_offset,
        )

    def _start_remote_fetch(self):
        """Fetch entries/treatments in a worker thread to keep UI responsive."""
        if 'entries_to_fetch' in self.config:
            entries_to_fetch = int(self.config['entries_to_fetch'])
        else:
            time_window_hours = self.config.get('time_window_hours', 1)
            entries_to_fetch = max(int(90 * time_window_hours), 30)

        payload = {
            "nightscout_url": self.nightscout_url,
            "api_secret": self.api_secret,
            "fetch_remote": True,
            "entries_to_fetch": entries_to_fetch,
            "entries_cache": list(self._entries_cache),
            "fetch_treatments": True,
            "treatments_to_fetch": int(self.config.get('treatments_to_fetch', 50)),
            "treatments_cache": list(self._treatments_cache),
        }
        if self._fetch_thread is not None:
            self._fetch_thread.submit(payload)

    def _merge_entries_cache(self, incoming_entries):
        entries_to_fetch = int(self.config.get('entries_to_fetch', 90))
        merged = list(self._entries_cache)
        existing_ids = {e.get('_id') for e in merged if isinstance(e, dict)}

        for entry in incoming_entries:
            if isinstance(entry, dict) and entry.get('_id') not in existing_ids:
                merged.append(entry)
                existing_ids.add(entry.get('_id'))

        merged.sort(key=lambda e: e.get('date', 0))
        if len(merged) > entries_to_fetch:
            merged = merged[-entries_to_fetch:]
        return merged

    def _merge_treatments_cache(self, incoming_treatments):
        treatments_to_fetch = int(self.config.get('treatments_to_fetch', 50))
        merged = list(self._treatments_cache)
        existing_ids = {t.get('_id') for t in merged if isinstance(t, dict)}

        for treatment in incoming_treatments:
            if isinstance(treatment, dict) and treatment.get('_id') not in existing_ids:
                merged.append(treatment)
                existing_ids.add(treatment.get('_id'))

        merged.sort(key=lambda t: t.get('created_at', ''))
        if len(merged) > treatments_to_fetch:
            merged = merged[-treatments_to_fetch:]
        return merged

    def _on_remote_fetch_result(self, data):
        incoming_entries = list(data.get("entries_cache", []))
        incoming_treatments = list(data.get("treatments_cache", []))

        self._entries_cache = self._merge_entries_cache(incoming_entries)
        self._treatments_cache = self._merge_treatments_cache(incoming_treatments)
        self.update_glucose(fetch_remote=False)

    def _on_remote_fetch_error(self, error_text):
        log.warning("Background fetch error: %s", error_text)
        if hasattr(self, '_tray'):
            self._tray.setToolTip(f"NSOverlay\n⚠ Background fetch failed: {error_text}")
        if not self._entries_cache:
            self.age_label.setText("⚠ fetch error")

    def _stop_fetch_thread(self):
        if self._fetch_thread is None:
            return
        if self._fetch_thread.isRunning():
            self._fetch_thread.stop()
            self._fetch_thread.wait(2000)
        self._fetch_thread = None

    def _stop_treatment_write_threads(self):
        threads = list(getattr(self, '_pending_treatment_threads', set()))
        for thread in threads:
            if thread.isRunning():
                thread.wait(2000)
        self._pending_treatment_threads.clear()
    
    def add_treatments_to_graph(self, treatments):
        """Add treatment markers to the graph"""
        if not treatments or not self.config.get('show_treatments', True):
            return

        graph_event_types = {'correction bolus', 'meal bolus', 'bolus', 'carb correction', 'carbs', 'exercise', 'basal injection'}
        treatments = [t for t in treatments if t.get('eventType', '').lower() in graph_event_types]
        if not treatments:
            return

        view_range = self.graph.getViewBox().viewRange()
        y_min, y_max = view_range[1]
        treatment_y_position = y_min + (y_max - y_min) * 0.08
        graph_px_h = self.graph.size().height()
        if graph_px_h > 0:
            paired_label_gap = max((y_max - y_min) * 24 / graph_px_h, 18)
        else:
            paired_label_gap = max((y_max - y_min) * 0.12, 18)
        
        treatment_styles = {
            'insulin': {'color': BOLUS_COLOR, 'symbol': '▼', 'size': 12},
            'carb': {'color': '#FFA500', 'symbol': '▲', 'size': 12},
            'exercise': {'color': '#9B59B6', 'symbol': '●', 'size': 10}
        }
        
        for treatment in treatments:
            try:
                created_at = treatment.get('created_at', treatment.get('timestamp'))
                if not created_at:
                    continue
                    
                timestamp = self._parse_ns_datetime(created_at)
                if timestamp is None:
                    continue
                
                local_timestamp = timestamp + timedelta(hours=self.timezone_offset)
                unix_timestamp = local_timestamp.timestamp()
                
                event_type = treatment.get('eventType', '').lower()
                insulin_amount = treatment.get('insulin', 0)
                carb_amount = treatment.get('carbs', 0)

                if event_type == 'basal injection':
                    basal_amount = treatment.get('notes', treatment.get('insulin', ''))
                    try:
                        basal_amount = float(basal_amount)
                    except (TypeError, ValueError):
                        basal_amount = None
                    label_str = f"▼{basal_amount}U" if basal_amount is not None else "▼Basal"
                    basal_color = '#80e8e0'

                    outline_item = pg.TextItem(label_str, color='black', anchor=(0.5, 1.0))
                    outline_item.setPos(unix_timestamp + 1, treatment_y_position - 1)
                    self.graph.addItem(outline_item)
                    self.treatment_items.append(outline_item)

                    text_item = pg.TextItem(label_str, color=basal_color, anchor=(0.5, 1.0))
                    text_item.setPos(unix_timestamp, treatment_y_position)
                    self.graph.addItem(text_item)
                    self.treatment_items.append(text_item)

                if insulin_amount and insulin_amount > 0:
                    style = treatment_styles['insulin']
                    text_content = f"{style['symbol']}{insulin_amount}U"
                    
                    # Adjust position based on whether there are carbs
                    if carb_amount and carb_amount > 0:
                        # If carbs exist, split the pair vertically with a zoom-aware gap.
                        bolus_y = treatment_y_position - paired_label_gap
                    else:
                        bolus_y = treatment_y_position
                    
                    outline_item = pg.TextItem(
                        text_content,
                        color='black',
                        anchor=(0.5, 1.0)
                    )
                    outline_item.setPos(unix_timestamp + 1, bolus_y - 1)
                    self.graph.addItem(outline_item)
                    self.treatment_items.append(outline_item)
                    
                    text_item = pg.TextItem(
                        text_content,
                        color=style['color'],
                        anchor=(0.5, 1.0)
                    )
                    text_item.setPos(unix_timestamp, bolus_y)
                    self.graph.addItem(text_item)
                    self.treatment_items.append(text_item)
                    
                    # Show carbs together with bolus if present
                    if carb_amount and carb_amount > 0:
                        carb_style = treatment_styles['carb']
                        carb_content = f"{carb_style['symbol']}{int(carb_amount)}g"
                        
                        carb_y = treatment_y_position + paired_label_gap
                        carb_outline = pg.TextItem(
                            carb_content,
                            color='black',
                            anchor=(0.5, 0.0)
                        )
                        carb_outline.setPos(unix_timestamp + 1, carb_y + 1)
                        self.graph.addItem(carb_outline)
                        self.treatment_items.append(carb_outline)
                        
                        carb_text = pg.TextItem(
                            carb_content,
                            color=carb_style['color'],
                            anchor=(0.5, 0.0)
                        )
                        carb_text.setPos(unix_timestamp, carb_y)
                        self.graph.addItem(carb_text)
                        self.treatment_items.append(carb_text)
                
                elif carb_amount and carb_amount > 0:
                    # Show carbs alone if there's no bolus
                    style = treatment_styles['carb']
                    text_content = f"{style['symbol']}{int(carb_amount)}g"
                    
                    outline_item = pg.TextItem(
                        text_content,
                        color='black',
                        anchor=(0.5, 0.0)
                    )
                    outline_item.setPos(unix_timestamp + 1, treatment_y_position + 1)
                    self.graph.addItem(outline_item)
                    self.treatment_items.append(outline_item)
                    
                    text_item = pg.TextItem(
                        text_content,
                        color=style['color'],
                        anchor=(0.5, 0.0)
                    )
                    text_item.setPos(unix_timestamp, treatment_y_position)
                    self.graph.addItem(text_item)
                    self.treatment_items.append(text_item)
                
                if 'exercise' in event_type:
                    style = treatment_styles['exercise']
                    
                    duration_minutes = treatment.get('duration', 30)
                    if isinstance(duration_minutes, str):
                        try:
                            duration_minutes = float(duration_minutes)
                        except ValueError:
                            duration_minutes = 30
                    
                    duration_seconds = duration_minutes * 60
                    end_timestamp = unix_timestamp + duration_seconds
                    
                    exercise_height = 12
                    exercise_y_bottom = y_min + (y_max - y_min) * 0.15
                    exercise_y_top = exercise_y_bottom + exercise_height
                    
                    bottom_curve = pg.PlotCurveItem([unix_timestamp, end_timestamp], [exercise_y_bottom, exercise_y_bottom])
                    top_curve = pg.PlotCurveItem([unix_timestamp, end_timestamp], [exercise_y_top, exercise_y_top])
                    
                    from PyQt6.QtGui import QColor, QBrush
                    transparent_color = QColor(style['color'])
                    transparent_color.setAlpha(60)
                    transparent_brush = QBrush(transparent_color)
                    
                    exercise_box = pg.FillBetweenItem(bottom_curve, top_curve, brush=transparent_brush)
                    self.graph.addItem(exercise_box)
                    self.treatment_items.append(exercise_box)
                    
                    exercise_name = "Exercise"
                    
                    if treatment.get('notes'):
                        exercise_name = treatment.get('notes').strip()
                    elif treatment.get('reason'):
                        exercise_name = treatment.get('reason').strip()
                    elif treatment.get('eventType') and treatment.get('eventType') != 'Exercise':
                        exercise_name = treatment.get('eventType').strip()
                    
                    if len(exercise_name) > 15:
                        exercise_name = exercise_name[:12] + "..."
                    
                    if duration_seconds > 120:
                        outline_label = pg.TextItem(
                            exercise_name,
                            color='black',
                            anchor=(0.5, 0.5)
                        )
                        text_x = unix_timestamp + (duration_seconds / 2) + 1
                        text_y = exercise_y_bottom + (exercise_height / 2) - 1
                        outline_label.setPos(text_x, text_y)
                        self.graph.addItem(outline_label)
                        self.treatment_items.append(outline_label)
                        
                        exercise_label = pg.TextItem(
                            exercise_name,
                            color='white',
                            anchor=(0.5, 0.5)
                        )
                        text_x = unix_timestamp + (duration_seconds / 2)
                        text_y = exercise_y_bottom + (exercise_height / 2)
                        exercise_label.setPos(text_x, text_y)
                        self.graph.addItem(exercise_label)
                        self.treatment_items.append(exercise_label)
                    
            except Exception as e:
                log.error("Error adding treatment marker: %s", e)
                continue
    
    def update_glucose(self, fetch_remote=True):
        try:
            if fetch_remote:
                self._start_remote_fetch()

            if not self._entries_cache:
                if fetch_remote:
                    self.label.setText("Loading...")
                    self.time_label.setText(datetime.now().strftime("%H:%M"))
                    self.age_label.setText("Fetching data...")
                    return
                raise ValueError("No cached glucose entries available")

            if self._entries_cache:
                latest = self._entries_cache[-1]
                log.debug("[ENTRIES] Latest cached: sgv=%s, dateString=%s, _id=%s", latest.get('sgv'), latest.get('dateString'), latest.get('_id'))

            render_key = self._build_render_key()
            if not fetch_remote and render_key == self._last_render_key:
                now = datetime.now().timestamp()
                self.current_time_line.setPos(now)
                self.current_max_time = now
                self._update_iob_overlay()
                return

            entries = list(self._entries_cache)

            glucose_values = []
            timestamps = []
            colors = []
            last_valid_direction = 'Flat'
            last_valid_entry_utc = None

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                sgv = entry.get("sgv")
                date_str = entry.get("dateString", "")
                if sgv is None or not date_str:
                    continue
                try:
                    sgv = int(sgv)
                except (TypeError, ValueError):
                    continue
                if not (20 <= sgv <= 600):
                    continue
                timestamp = self._parse_ns_datetime(date_str)
                if timestamp is None:
                    log.warning("Could not parse dateString %r, skipping entry", date_str)
                    continue
                local_timestamp = timestamp + timedelta(hours=self.timezone_offset)
                unix_timestamp = local_timestamp.timestamp()
                glucose_values.append(sgv)
                timestamps.append(unix_timestamp)
                last_valid_direction = entry.get('direction', 'Flat') or 'Flat'
                last_valid_entry_utc = timestamp

            if not glucose_values:
                raise ValueError("No valid glucose entries found in API response")

            # Precompute actual extreme values for gradient anchoring
            target_low = self.config['target_low']
            target_high = self.config['target_high']
            glucose_colors = self.config['appearance']['colors']['glucose_ranges']

            above_high = [v for v in glucose_values if v > target_high]
            below_low  = [v for v in glucose_values if v < target_low]
            sgv_max = max(above_high) if above_high else None
            sgv_min = min(below_low)  if below_low  else None

            colors = [
                self.get_glucose_color_with_interpolation(
                    sgv, target_low, target_high, glucose_colors,
                    sgv_max=sgv_max, sgv_min=sgv_min
                )
                for sgv in glucose_values
            ]

            current_now = datetime.now().timestamp()
            self.current_time_line.setPos(current_now)

            if self._value_text_item is not None:
                try:
                    self.graph.removeItem(self._value_text_item)
                except Exception:
                    pass
                self._value_text_item = None
            
            glucose_colors = self.config['appearance']['colors']['glucose_ranges']
            target_low = self.config['target_low']
            target_high = self.config['target_high']
            
            _line_style_map = {
                'solid': Qt.PenStyle.SolidLine,
                'dash': Qt.PenStyle.DashLine,
                'dot': Qt.PenStyle.DotLine,
                'dashdot': Qt.PenStyle.DashDotLine,
            }
            _style_key = self.config['appearance'].get('graph_line_style', 'solid')
            _pen_style = _line_style_map.get(_style_key, Qt.PenStyle.SolidLine)
            _lw = self.config['appearance']['graph_line_width']
            _smooth = self.config['appearance'].get('graph_line_smooth', False)

            if _smooth and len(timestamps) >= 2:
                import numpy as _np
                pts_x = _np.array(timestamps, dtype=float)
                pts_y = _np.array(glucose_values, dtype=float)
                n = len(pts_x)
                _num_sub = 36  # denser interpolation keeps the curve visually smooth at common zoom levels

                # Monotone cubic (Fritsch-Carlson) interpolation.
                # Guarantees no Y overshoot and always moves forward on X.
                dx = _np.diff(pts_x)
                dy = _np.diff(pts_y)
                with _np.errstate(invalid='ignore', divide='ignore'):
                    delta = _np.where(dx != 0, dy / dx, 0.0)

                m = _np.empty(n)
                m[0] = delta[0]
                m[-1] = delta[-1]
                m[1:-1] = (delta[:-1] + delta[1:]) / 2.0

                # Vectorised Fritsch-Carlson monotonicity correction.
                # Use masked division to avoid divide-by-zero warnings on flat segments.
                alpha = _np.zeros_like(delta)
                beta = _np.zeros_like(delta)
                _np.divide(m[:-1], delta, out=alpha, where=delta != 0)
                _np.divide(m[1:], delta, out=beta, where=delta != 0)
                r2 = alpha * alpha + beta * beta
                mask = r2 > 9
                tau = _np.where(mask, 3.0 / _np.sqrt(_np.where(r2 > 0, r2, 1.0)), 1.0)
                m[:-1] = _np.where(mask, tau * alpha * delta, m[:-1])
                m[1:]  = _np.where(mask, tau * beta  * delta, m[1:])
                zero_mask = delta == 0
                m[:-1] = _np.where(zero_mask, 0.0, m[:-1])
                m[1:]  = _np.where(zero_mask, 0.0, m[1:])

                # Build all sub-points with vectorised Hermite evaluation
                _t = _np.linspace(0.0, 1.0, _num_sub + 1)
                _t2, _t3 = _t * _t, _t * _t * _t
                h00 =  2*_t3 - 3*_t2 + 1   # shape (_num_sub+1,)
                h10 =    _t3 - 2*_t2 + _t
                h01 = -2*_t3 + 3*_t2
                h11 =    _t3 -   _t2

                # For each segment build x/y arrays; drop last point except on final segment
                x_parts = []
                y_parts = []
                for i in range(n - 1):
                    h  = dx[i]
                    sx = pts_x[i] + _t * h
                    sy = (h00 * pts_y[i] + h10 * h * m[i] +
                          h01 * pts_y[i+1] + h11 * h * m[i+1])
                    if i < n - 2:
                        x_parts.append(sx[:-1])
                        y_parts.append(sy[:-1])
                    else:
                        x_parts.append(sx)
                        y_parts.append(sy)

                all_sub_x = _np.concatenate(x_parts)
                all_sub_y = _np.concatenate(y_parts)

                # Color each sub-point, then batch same-color runs into
                # a single PlotCurveItem (cheaper than self.graph.plot()).
                sub_colors = [
                    self.get_glucose_color_with_interpolation(
                        float(y_val), target_low, target_high, glucose_colors,
                        sgv_max=sgv_max, sgv_min=sgv_min
                    )
                    for y_val in all_sub_y
                ]
                segments = []
                idx = 0
                total = len(all_sub_x)
                while idx < total - 1:
                    cur_col = sub_colors[idx]
                    j = idx + 1
                    while j < total - 1 and sub_colors[j] == cur_col:
                        j += 1
                    segments.append((all_sub_x[idx:j+1], all_sub_y[idx:j+1], cur_col))
                    idx = j

                self._apply_line_segments(segments, _lw, _pen_style)
            else:
                segment_colors = []
                for i in range(len(timestamps) - 1):
                    avg_glucose = (glucose_values[i] + glucose_values[i + 1]) / 2
                    segment_colors.append(
                        self.get_glucose_color_with_interpolation(
                            avg_glucose, target_low, target_high, glucose_colors,
                            sgv_max=sgv_max, sgv_min=sgv_min
                        )
                    )

                segments = []
                idx = 0
                seg_total = len(segment_colors)
                while idx < seg_total:
                    cur_color = segment_colors[idx]
                    j = idx + 1
                    while j < seg_total and segment_colors[j] == cur_color:
                        j += 1
                    segments.append((timestamps[idx:j + 1], glucose_values[idx:j + 1], cur_color))
                    idx = j

                self._apply_line_segments(segments, _lw, _pen_style)
            
            dot_size = self.get_adaptive_dot_size()
            marker_outline_color = self.config['appearance']['marker_outline_color']
            marker_outline_width = self.config['appearance']['marker_outline_width']
            self._scatter_item.setData(
                x=timestamps,
                y=glucose_values,
                symbol='o',
                size=dot_size,
                pen=pg.mkPen(marker_outline_color, width=marker_outline_width),
                brush=[pg.mkBrush(c) for c in colors],
            )
            
            if timestamps and glucose_values:
                newest_x = timestamps[-1]
                newest_y = glucose_values[-1]
                newest_color = colors[-1]
                
                view_range = self.graph.getViewBox().viewRange()
                x_min, x_max = view_range[0]
                y_min, y_max = view_range[1]
                
                time_offset = 2 * 60
                text_x = newest_x - time_offset
                
                # Ensure text X position is within visible bounds
                text_margin = 30
                if text_x < x_min + text_margin:
                    text_x = x_min + text_margin
                elif text_x > x_max - text_margin:
                    text_x = x_max - text_margin
                
                # Dynamic vertical positioning to avoid overlaps
                text_y_offset = 12
                
                if len(glucose_values) >= 2:
                    check_range = min(8, len(glucose_values))
                    recent_points = list(zip(timestamps[-check_range:], glucose_values[-check_range:]))
                    
                    text_height = 15
                    text_above_y = newest_y + 12
                    text_below_y = newest_y - 20
                    
                    overlap_above = False
                    overlap_below = False
                    
                    for point_x, point_y in recent_points[:-1]:
                        time_diff = abs(point_x - text_x)
                        if time_diff <= 3 * 60:
                            if abs(point_y - text_above_y) <= text_height:
                                overlap_above = True
                            if abs(point_y - text_below_y) <= text_height:
                                overlap_below = True
                    
                    if not overlap_above and not overlap_below:
                        if len(glucose_values) >= 2:
                            if glucose_values[-1] >= glucose_values[-2]:
                                text_y_offset = 18
                            else:
                                text_y_offset = -25
                        else:
                            text_y_offset = 18
                    elif not overlap_above:
                        text_y_offset = 18
                    elif not overlap_below:
                        text_y_offset = -25
                    else:
                        # Both positions have overlaps, choose the one with less conflict
                        text_y_offset = 25
                
                text_y = newest_y + text_y_offset
                
                y_margin = 10
                if text_y > y_max - y_margin:
                    text_y = y_max - y_margin
                elif text_y < y_min + y_margin:
                    text_y = y_min + y_margin

                # After clamping, ensure the label never sits on top of the dot.
                # Convert the needed pixel clearance (dot radius + text box half-height + gap)
                # to data-units using the current Y scale.
                dot_px = self.get_adaptive_dot_size()
                clearance_px = dot_px / 2 + 14 + 6  # dot radius + text half-height + gap
                graph_px_h = self.graph.size().height()
                min_clearance = (y_max - y_min) * clearance_px / graph_px_h if graph_px_h > 0 else (y_max - y_min) * 0.12
                if abs(text_y - newest_y) < min_clearance:
                    room_above = (y_max - y_margin) - newest_y
                    room_below = newest_y - (y_min + y_margin)
                    if room_above >= room_below:
                        text_y = min(newest_y + min_clearance, y_max - y_margin)
                    else:
                        text_y = max(newest_y - min_clearance, y_min + y_margin)
                
                if self.config.get('show_float_glucose', True):
                    text_item = pg.TextItem(
                        text=f"{newest_y}", 
                        color=newest_color,
                        fill=self._get_cached_brush(QColor(0, 0, 0, 180)),
                        border=self._get_cached_pen(newest_color, width=1),
                        anchor=(0.5, 0.5)
                    )
                    text_item.setFont(pg.QtGui.QFont("sans-serif", 10, pg.QtGui.QFont.Weight.Bold))
                    text_item.setPos(text_x, text_y)
                    self.graph.addItem(text_item)
                    self._value_text_item = text_item

            current = glucose_values[-1]
            
            delta_text = ""
            if self.config.get('show_delta', True) and len(timestamps) >= 2:
                glucose_5min_ago = self.interpolate_glucose_5min_ago(timestamps, glucose_values)
                if glucose_5min_ago is not None:
                    delta = current - glucose_5min_ago
                    if delta > 0:
                        delta_text = f" (+{delta})"
                    elif delta < 0:
                        delta_text = f" ({delta})"
                    else:
                        delta_text = " (0)"
            
            trend_arrow = self.convert_nightscout_trend(last_valid_direction)
            glucose_text = f"{current} {trend_arrow}{delta_text}"
            self.label.setText(glucose_text)
            
            self.auto_resize_to_fit_content(glucose_text)
            
            current_time = datetime.now()
            self.time_label.setText(current_time.strftime("%H:%M"))
            
            if last_valid_entry_utc:
                now_utc = datetime.utcnow()
                self.last_entry_time = last_valid_entry_utc
                age_seconds = int((now_utc - last_valid_entry_utc).total_seconds())
                
                if age_seconds < 60:
                    age_text = f"{age_seconds} sec ago"
                elif age_seconds < 3600:
                    age_minutes = age_seconds // 60
                    age_text = f"{age_minutes} min ago"
                else:
                    age_hours = age_seconds // 3600
                    age_text = f"{age_hours} hr ago"
                    
                self.age_label.setText(age_text)
            
            self.update_color(current)
            self._update_tray_icon(current, trend_arrow, delta_text)

            y_min, y_max = self._compute_y_range(glucose_values)
            self.graph.setYRange(y_min, y_max, padding=0)  # type: ignore[call-arg]

            # Treatments come from background cache updates.
            all_treatments = list(self._treatments_cache)

            if self.config.get('show_treatments', True):
                treatment_render_key = self._build_treatment_render_key(all_treatments, y_min, y_max)
                if treatment_render_key != self._last_treatment_render_key:
                    self.clear_treatments()
                    self.add_treatments_to_graph(all_treatments)
                    self._last_treatment_render_key = treatment_render_key
            else:
                if self.treatment_items:
                    self.clear_treatments()
                self._last_treatment_render_key = None

            self._update_header_pills(all_treatments)

            now = datetime.now().timestamp()
            time_window_seconds = self.config['time_window_hours'] * 60 * 60
            window_start = now - time_window_seconds
            # Add right padding to prevent clipping of rightmost data points and text
            right_padding = 5 * 60
            self.graph.setXRange(window_start, now + right_padding)  # type: ignore[call-arg]
            
            if self.current_time_line is None:
                # This should not happen anymore since we create it above, but safety check
                time_line_color = self.config['appearance']['colors']['graph']['current_time_line']
                self.current_time_line = pg.InfiniteLine(
                    pos=now,
                    angle=90,
                    pen=pg.mkPen(color=time_line_color, width=1, cosmetic=True),
                    movable=False
                )
                self.graph.addItem(self.current_time_line)
                self.current_time_line.setZValue(1000)
            else:
                self.current_time_line.setPos(now)

            self._update_iob_overlay()
            
            self.current_max_time = now
            self._last_render_key = render_key

        except requests.exceptions.RequestException as e:
            log.warning("update_glucose error: %s", e)
            # Classify the error for a meaningful label
            if isinstance(e, requests.exceptions.ConnectionError):
                msg = "⚠ no connection"
                tip = "⚠ No connection — check network"
            elif isinstance(e, requests.exceptions.Timeout):
                msg = "⚠ timeout"
                tip = "⚠ Request timed out"
            elif isinstance(e, requests.exceptions.HTTPError):
                code = e.response.status_code if e.response is not None else "?"
                if code in (401, 403):
                    msg = f"⚠ auth error ({code})"
                    tip = f"⚠ HTTP {code} — check API secret"
                elif code == 404:
                    msg = "⚠ URL not found"
                    tip = "⚠ HTTP 404 — check Nightscout URL"
                elif isinstance(code, int) and code >= 500:
                    msg = f"⚠ server error ({code})"
                    tip = f"⚠ HTTP {code} — Nightscout server error"
                else:
                    msg = f"⚠ HTTP {code}"
                    tip = f"⚠ HTTP {code}: {e}"
            else:
                msg = "⚠ fetch error"
                tip = f"⚠ Error: {e}"
            # Keep the last glucose reading visible — muted to signal degraded state.
            self.label.setStyleSheet(
                "color: #8b96aa; background-color: transparent; "
                "font-weight: bold; padding: 0px 4px;"
            )
            self.age_label.setText(msg)
            self.age_label.setStyleSheet(
                "color: #ff7a7a; background-color: transparent; padding: 0px 4px;"
            )
            if hasattr(self, '_tray'):
                self._tray.setToolTip(f"NSOverlay\n{tip}")
            self._apply_widget_background()
            self._apply_header_background()
        except Exception as e:
            log.warning("update_glucose error: %s", e)
            self.label.setStyleSheet(
                "color: #8b96aa; background-color: transparent; "
                "font-weight: bold; padding: 0px 4px;"
            )
            self.age_label.setText("⚠ fetch error")
            self.age_label.setStyleSheet(
                "color: #ff7a7a; background-color: transparent; padding: 0px 4px;"
            )
            if hasattr(self, '_tray'):
                self._tray.setToolTip(f"NSOverlay\n⚠ Error: {e}")
            self._apply_widget_background()
            self._apply_header_background()

    def interpolate_glucose_5min_ago(self, timestamps, glucose_values):
        """Interpolate glucose value from 5 minutes ago using linear interpolation"""
        if len(timestamps) < 2 or len(glucose_values) < 2:
            return None
            
        latest_time = timestamps[-1]
        target_time = latest_time - (5 * 60)
        
        if target_time < timestamps[0]:
            return None
            
        if target_time >= timestamps[-1]:
            return glucose_values[-1]
            
        for i in range(len(timestamps) - 1):
            if timestamps[i] <= target_time <= timestamps[i + 1]:
                t1, t2 = timestamps[i], timestamps[i + 1]
                v1, v2 = glucose_values[i], glucose_values[i + 1]
                
                if t2 == t1:
                    return v1
                    
                ratio = (target_time - t1) / (t2 - t1)
                interpolated_value = v1 + ratio * (v2 - v1)
                return round(interpolated_value)
                
        return None

    def convert_nightscout_trend(self, direction):
        """Convert Nightscout trend to appropriate arrow symbol"""
        trend_map = {
            'DoubleUp': '↑↑',
            'SingleUp': '↑',
            'FortyFiveUp': '↗',
            'Flat': '→',
            'FortyFiveDown': '↘',
            'SingleDown': '↓',
            'DoubleDown': '↓↓',
            'None': '→',
            'NOT COMPUTABLE': '→',
            'RATE OUT OF RANGE': '→'
        }
        return trend_map.get(direction, '→')

    def update_time_display(self):
        """Update current time and entry age without fetching new data"""
        try:
            current_time = datetime.now()
            time_text = current_time.strftime("%H:%M")
            if time_text != self._last_time_text:
                self.time_label.setText(time_text)
                self._last_time_text = time_text
            
            if hasattr(self, 'current_time_line') and self.current_time_line is not None:
                self.current_time_line.setPos(current_time.timestamp())
            
            if hasattr(self, 'last_entry_time') and self.last_entry_time:
                now_utc = datetime.utcnow()
                age_seconds = int((now_utc - self.last_entry_time).total_seconds())
                
                if age_seconds < 60:
                    age_text = f"{age_seconds} sec ago"
                elif age_seconds < 3600:
                    age_minutes = age_seconds // 60
                    age_text = f"{age_minutes} min ago"
                else:
                    age_hours = age_seconds // 3600
                    age_text = f"{age_hours} hr ago"
                    
                is_stale = age_seconds > 900
                display_age_text = f"⚠ {age_text}" if is_stale else age_text
                if display_age_text != self._last_age_text:
                    self.age_label.setText(display_age_text)
                    self._last_age_text = display_age_text
                
                # Update age label color based on freshness
                if age_seconds <= 300:  # 5 minutes or less
                    age_color = "#37d39a"  # Fresh
                elif age_seconds <= 900:  # 15 minutes or less
                    age_color = "#f2c14e"  # Warning
                else:
                    age_color = "#ff7a7a"  # Stale
                if age_color != self._last_age_color:
                    self.age_label.setStyleSheet(
                        f"color: {age_color}; background-color: transparent; padding: 0px 4px;"
                    )
                    self._last_age_color = age_color

                # Grey out glucose label when stale; restore when fresh
                if is_stale != self._last_glucose_stale_state:
                    self._last_glucose_stale_state = is_stale
                    if is_stale:
                        self.label.setStyleSheet(
                            "color: #8b96aa; background-color: transparent; "
                            "font-weight: bold; padding: 0px 4px;"
                        )
                    elif hasattr(self, '_last_glucose_color'):
                        self.label.setStyleSheet(
                            f"color: {self._last_glucose_color}; background-color: transparent; "
                            "font-weight: bold; padding: 0px 4px;"
                        )

                # Refresh tray icon when staleness state changes (fresh→stale)
                now_stale = age_seconds > 900
                was_stale = getattr(self, '_tray_was_stale', None)
                if now_stale != was_stale and hasattr(self, '_last_tray_glucose'):
                    self._update_tray_icon(
                        self._last_tray_glucose,
                        self._last_tray_trend,
                        self._last_tray_delta,
                    )
        except Exception as e:
            log.debug("update_time_display error: %s", e)
    
    def _compute_y_range(self, glucose_values):
        """Compute Y axis range that always shows target lines and the current reading."""
        target_low = self.config['target_low']
        target_high = self.config['target_high']

        # Anchor points that must always be visible
        anchors = [target_low, target_high]
        if glucose_values:
            anchors.append(glucose_values[-1])   # current reading
            anchors.extend(glucose_values)        # full dataset

        data_min = min(anchors)
        data_max = max(anchors)

        glucose_range = data_max - data_min
        padding = max(glucose_range * 0.15, 20)

        y_min = max(40, data_min - padding)
        y_max = min(400, data_max + padding)

        if y_max - y_min < 100:
            center = (y_min + y_max) / 2
            y_min = center - 50
            y_max = center + 50

        return y_min, y_max

    def calculate_adaptive_y_range(self):
        """Calculate appropriate Y range based on current glucose data"""
        try:
            if self._entries_cache:
                glucose_values = []
                for entry in self._entries_cache:
                    sgv = entry.get("sgv") if isinstance(entry, dict) else None
                    if isinstance(sgv, (int, float)):
                        glucose_values.append(int(sgv))
                if glucose_values:
                    return self._compute_y_range(glucose_values)
        except Exception:
            pass
        
        return 40, 300
    
    def center_graph(self, event):
        """Center the graph on the current time window and reset Y zoom when double-clicked"""
        try:
            now = datetime.now().timestamp()
            time_window_seconds = self.config['time_window_hours'] * 60 * 60
            window_start = now - time_window_seconds
            # Add right padding to prevent clipping of rightmost data points and text
            right_padding = 5 * 60
            self.graph.setXRange(window_start, now + right_padding)  # type: ignore[call-arg]
            
            y_min, y_max = self.calculate_adaptive_y_range()
            self.graph.setYRange(y_min, y_max, padding=0.05)  # type: ignore[call-arg]
            
            self.current_max_time = now
            
            if hasattr(self, 'current_time_line') and self.current_time_line is not None:
                self.current_time_line.setPos(now)
        except:
            pass
    
    def on_range_changed(self):
        """Handle range changes for both time limiting and adaptive sizing"""
        self.limit_time_range()
        self.update_adaptive_sizing()
        self._update_iob_overlay()
        self.save_zoom_state()
    
    def get_adaptive_dot_size(self):
        """Calculate adaptive dot size based on Y-axis zoom level"""
        if not self.config.get('adaptive_dot_size', False):
            return self.config['data_point_size']
            
        try:
            if self.current_y_range is None:
                return self.config['data_point_size']
            
            current_range = self.graph.viewRange()[1]
            y_span = current_range[1] - current_range[0]
            
            base_size = self.config['data_point_size']
            scale_factor = max(0.5, min(3.0, 260 / y_span))
            
            return int(base_size * scale_factor)
        except:
            return self.config['data_point_size']
    
    def update_adaptive_sizing(self):
        """Update dot sizes when zoom changes"""
        try:
            self.current_y_range = self.graph.viewRange()[1]
        except:
            pass
    
    def limit_time_range(self):
        """Prevent scrolling to future times (beyond padding)"""
        try:
            current_range = self.graph.viewRange()[0]
            max_time = getattr(self, 'current_max_time', datetime.now().timestamp())
            right_padding = 5 * 60
            max_allowed_time = max_time + right_padding
            
            if current_range[1] > max_allowed_time:
                time_window = current_range[1] - current_range[0]
                new_end = max_allowed_time
                new_start = new_end - time_window
                self.graph.setXRange(new_start, new_end, padding=0)  # type: ignore[call-arg]
        except:
            pass

    # ===== Color Logic =====
    def update_color(self, sgv):
        target_low = self.config['target_low']
        target_high = self.config['target_high']
        glucose_colors = self.config['appearance']['colors']['glucose_ranges']
        
        glucose_color = self.get_glucose_color_with_interpolation(sgv, target_low, target_high, glucose_colors)
        
        if sgv < target_low:
            text_color = "#ff6666"
        elif target_low <= sgv <= target_high:
            text_color = glucose_color
        else:
            text_color = "#ffaa44"

        self._last_glucose_color = text_color
        self._apply_widget_background()
        self._apply_header_background()
        self._apply_header_label_styles(glucose_text_color=text_color)

if __name__ == "__main__":
    import ctypes
    try:
        _f = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        _f.argtypes = [ctypes.c_wchar_p]
        _f.restype = ctypes.c_long
        _f("NSOverlay.App")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("NSOverlay")
    app.setApplicationDisplayName("NSOverlay")
    # Keep the process alive when the main window is hidden (minimised to tray)
    app.setQuitOnLastWindowClosed(False)

    _icon_path = os.path.join(_BASE_DIR, "icon.ico")
    if os.path.exists(_icon_path):
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(_icon_path))

    if not os.path.exists(CONFIG_FILE):
        wizard = SetupWizard(CONFIG_FILE, DARK_QSS)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)

    widget = GlucoseWidget()
    widget.show()

    # Set AppUserModelID directly on the window handle — this is what
    # the taskbar actually reads and is more reliable than the process-level call.
    try:
        from win32com.propsys import propsys, pscon  # type: ignore[import]
        store = propsys.SHGetPropertyStoreForWindow(int(widget.winId()))  # type: ignore[call-arg, attr-defined]
        store.SetValue(pscon.PKEY_AppUserModel_ID,
                       propsys.PROPVARIANTType("NSOverlay.App"))
        store.Commit()
    except Exception:
        pass

    sys.exit(app.exec())
