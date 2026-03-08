import sys
import os
import json
import hashlib
import logging
import logging.handlers
import requests
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QMenu,
                             QSlider, QSpinBox, QDoubleSpinBox, QPushButton, QDialog, QFormLayout,
                             QGroupBox, QLineEdit, QTabWidget, QCheckBox, QComboBox, QListWidget,
                             QScrollArea, QColorDialog, QFrame, QGraphicsDropShadowEffect,
                             QSystemTrayIcon)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QAction, QFontMetrics, QPixmap, QPainter, QIcon
import pyqtgraph as pg

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



class TimeAxisItem(pg.AxisItem):
    """Custom axis item to display time in 24-hour format."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def tickStrings(self, values, scale, spacing):
        """Override to return 24-hour time format"""
        strings = []
        for v in values:
            try:
                dt = datetime.fromtimestamp(v)
                strings.append(dt.strftime("%H:%M"))
            except (ValueError, OSError):
                strings.append("")
        return strings



def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"config.json not found at: {CONFIG_FILE}")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"config.json is not valid JSON: {e}")

    url = config.get("nightscout_url", "").strip().rstrip("/")
    secret = config.get("api_secret", "").strip()

    if not url:
        raise ValueError("nightscout_url is missing or empty in config.json")
    if not secret:
        raise ValueError("api_secret is missing or empty in config.json")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"nightscout_url must start with http:// or https://, got: {url!r}")

    # Validate numeric range settings
    target_low = config.get("target_low", 70)
    target_high = config.get("target_high", 180)
    if not isinstance(target_low, (int, float)) or not isinstance(target_high, (int, float)):
        raise ValueError("target_low and target_high must be numbers")
    if target_low >= target_high:
        raise ValueError(f"target_low ({target_low}) must be less than target_high ({target_high})")

    # Hash secret (Nightscout requires SHA1)
    hashed_secret = hashlib.sha1(secret.encode()).hexdigest()
    
    _default_appearance = {
        "marker_outline_width": 1.5,
        "marker_outline_color": "#000000",
        "graph_line_width": 2,
        "graph_line_style": "solid",
        "show_y_label": True,
        "target_zone_opacity": 20,
        "grid_opacity": 0.3,
        "background_color": "#1a1a1a",
        "graph_background_opacity": 100,
        "label_pill_opacity": 67,
        "colors": {
            "ui": {
                "main_glucose_text": "#ffffff",
                "time_label": "#cccccc",
                "age_label": "#999999",
                "close_button": "#ff4444",
                "close_button_hover": "#ff6666",
                "close_button_background": "rgba(0, 0, 0, 150)",
                "close_button_hover_background": "rgba(255, 68, 68, 200)",
                "widget_background": "#2a2a2a"
            },
            "graph": {
                "axis_lines": "#888888",
                "axis_text": "#cccccc",
                "axis_labels": "#cccccc",
                "current_time_line": "#888888",
                "main_line": "#a0a0a0",
                "background": "#1a1a1a"
            },
            "glucose_ranges": {
                "low": "#ff4444",
                "in_range": "#00d4aa",
                "high": "#ff8800"
            },
            "target_zones": {
                "low_line": "#ff4444",
                "high_line": "#ff8800",
                "target_fill": "#00d4aa"
            }
        }
    }

    def _deep_merge(base, override):
        """Merge override into base, returning the merged dict."""
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = _deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    appearance = _deep_merge(_default_appearance, config.get("appearance", {}))

    settings = {
        'refresh_interval': max(5000, int(config.get("refresh_interval_ms", 10000))),
        'timezone_offset': float(config.get("timezone_offset_hours", 0)),
        'time_window_hours': max(0.25, float(config.get("time_window_hours", 3))),
        'target_low': target_low,
        'target_high': target_high,
        'widget_width': max(150, int(config.get("widget_width", 400))),
        'widget_height': max(100, int(config.get("widget_height", 280))),
        'glucose_font_size': max(8, int(config.get("glucose_font_size", 18))),
        'time_font_size': max(6, int(config.get("time_font_size", 12))),
        'age_font_size': max(6, int(config.get("age_font_size", 10))),
        'data_point_size': max(2, int(config.get("data_point_size", 6))),
        'show_treatments': bool(config.get("show_treatments", True)),
        'treatments_to_fetch': max(1, min(500, int(config.get("treatments_to_fetch", 50)))),
        'gradient_interpolation': bool(config.get("gradient_interpolation", True)),
        'header_pills': config.get('header_pills', []),
        'appearance': appearance
    }

    return url, hashed_secret, secret, settings


class SetupWizard(QDialog):
    """First-run wizard: asks for Nightscout URL and API secret, writes config.json."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NSOverlay — First Run Setup")
        self.setFixedSize(480, 300)
        self.setWindowFlags(Qt.WindowType.Dialog)

        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("Welcome to NSOverlay")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Enter your Nightscout credentials to get started.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #7070aa; font-size: 10px;")
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://your-site.fly.dev")
        self.url_input.setMinimumHeight(30)
        form.addRow("Nightscout URL:", self.url_input)

        self.secret_input = QLineEdit()
        self.secret_input.setPlaceholderText("Your API secret (plain text)")
        self.secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_input.setMinimumHeight(30)
        form.addRow("API Secret:", self.secret_input)

        layout.addLayout(form)
        layout.addSpacing(6)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #ff4444;")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(34)
        cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save && Start")
        self.save_btn.setMinimumHeight(34)
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)
        self.setStyleSheet(DARK_QSS)
        title.setStyleSheet("color: #00d4aa; font-size: 14pt; font-weight: bold; background: transparent;")

    def _save(self):
        url = self.url_input.text().strip().rstrip("/")
        secret = self.secret_input.text().strip()

        if not url:
            self.status_label.setText("Nightscout URL is required.")
            return
        if not url.startswith(("http://", "https://")):
            self.status_label.setText("URL must start with https:// or http://")
            return
        if not secret:
            self.status_label.setText("API secret is required.")
            return

        config = {
            "nightscout_url": url,
            "api_secret": secret,
            "refresh_interval_ms": 10000,
            "timezone_offset_hours": 0,
            "time_window_hours": 3,
            "target_low": 70,
            "target_high": 180,
            "widget_width": 400,
            "widget_height": 280,
            "glucose_font_size": 18,
            "time_font_size": 12,
            "age_font_size": 10,
            "data_point_size": 6,
            "show_treatments": True,
            "treatments_to_fetch": 50,
            "gradient_interpolation": False
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
            self.accept()
        except Exception as e:
            self.status_label.setText(f"Could not write config: {e}")
            self.save_btn.setEnabled(True)


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
        self.setObjectName("GlucoseWidget")

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        # Required for graph background transparency to show through
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Store original configured dimensions
        self.base_width = self.config['widget_width']
        self.base_height = self.config['widget_height']
        self.user_resized = False
        self.setMinimumSize(200, 150)
        self.setMaximumSize(800, 600)
        
        self.resize(self.base_width, self.base_height)
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(8, 8, 8, 8)
        self.main_layout.setSpacing(3)
        self.setLayout(self.main_layout)

        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(20)
        
        self.left_info_layout = QVBoxLayout()
        self.left_info_layout.setContentsMargins(0, 0, 0, 0)
        self.left_info_layout.setSpacing(1)
        
        self.time_label = QLabel("")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setFont(QFont("Segoe UI", self.config['time_font_size'], QFont.Weight.Medium))
        self.time_label.setStyleSheet("color: #cccccc;")
        
        self.age_label = QLabel("")
        self.age_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.age_label.setFont(QFont("Segoe UI", self.config['age_font_size'] - 1, QFont.Weight.Normal))
        self.age_label.setStyleSheet("color: #999999;")
        
        self.left_info_layout.addWidget(self.time_label)
        self.left_info_layout.addWidget(self.age_label)
        
        self.label = QLabel("Loading...")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setFont(QFont("Segoe UI", self.config['glucose_font_size'], QFont.Weight.Bold))
        self.label.setStyleSheet("color: #ffffff;")
        
        # Pills container – sits on the left edge of the header row
        self.pills_layout = QHBoxLayout()
        self.pills_layout.setContentsMargins(0, 0, 0, 0)
        self.pills_layout.setSpacing(5)
        self.header_pills_widget = QWidget()
        self.header_pills_widget.setLayout(self.pills_layout)
        self.header_pills_widget.setStyleSheet("background: transparent;")
        self.header_pills_widget.hide()

        self.header_layout.addWidget(self.header_pills_widget, 0)
        self.header_layout.addStretch(1)
        self.header_layout.addLayout(self.left_info_layout, 0)
        self.header_layout.addWidget(self.label, 0)
        
        header_widget = QWidget()
        header_widget.setLayout(self.header_layout)
        header_widget.setFixedHeight(40)
        header_widget.setStyleSheet("background: transparent;")
        self.main_layout.addWidget(header_widget)

        self.close_button = QLabel("✕", self)
        self.close_button.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.close_button.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        close_button_hover = self.config['appearance']['colors']['ui']['close_button_hover']

        # WA_TranslucentBackground means mouse events are skipped over transparent pixels,
        # so hide/show on enter/leave is unreliable — poll instead.
        self.close_button.setStyleSheet(f"""
            QLabel {{
                color: rgba(255, 255, 255, 55);
                background-color: rgba(20, 20, 20, 100);
                border-radius: 15px;
                padding: 2px;
                margin: 0px;
                border: 1px solid rgba(255, 255, 255, 20);
            }}
            QLabel:hover {{
                background-color: rgba(190, 30, 30, 230);
                color: rgba(255, 255, 255, 255);
                border: 1px solid rgba(255, 90, 90, 140);
            }}
        """)
        self.close_button.setFixedSize(30, 30)
        self.close_button.hide()
        self.close_button.mousePressEvent = lambda ev: (self.close(), None)[1]  # type: ignore[method-assign]

        self.setup_graph()
        self._apply_header_label_styles()
        self.setup_shortcuts()

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
        self.resize_edge = None
        self.resize_start_pos = None
        self.resize_start_geometry = None
        
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
        
    def resizeEvent(self, event):
        """Position the close button in top-right corner when widget is resized"""
        super().resizeEvent(event)
        if hasattr(self, 'close_button'):
            self.close_button.move(self.width() - 32, 3)
        
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
        inside = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if hasattr(self, 'close_button'):
            if inside and not self.close_button.isVisible():
                self.close_button.show()
                self.close_button.raise_()
            elif not inside and self.close_button.isVisible():
                self.close_button.hide()

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
        opacity = self.config['appearance'].get('graph_background_opacity', 100)
        bg_color = self.config['appearance'].get('background_color', '#1a1a1a')
        color = QColor(bg_color)
        color.setAlpha(round(opacity / 100 * 255))
        self.graph.setBackground(color)

    def _apply_widget_background(self):
        """Apply widget background color with configured opacity (matches graph opacity).
        Uses the object-name selector so the rule does NOT cascade to child widgets."""
        opacity = self.config['appearance'].get('graph_background_opacity', 100)
        widget_bg = self.config['appearance']['colors']['ui']['widget_background']
        color = QColor(widget_bg)
        alpha = round(opacity / 100 * 255)
        rgba = f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        self.setStyleSheet(f"#GlucoseWidget {{ background-color: {rgba}; }}")

    def _update_header_pills(self, treatments):
        """Rebuild header pill labels based on the latest treatments and current config.

        Each entry in config['header_pills'] may have:
          event_type    (str, required)  – matches treatment eventType (case-insensitive)
          label         (str)            – display label; defaults to event_type
          show_field    (str)            – treatment field whose value is shown/summed
          suffix        (str)            – text appended after the value (e.g. "U", "g")
          color         (str)            – pill background color (default "#4a9eff")
          max_age_hours (number)         – how old the treatment may be (default 24); ignored when sum_daily=true
          sum_daily     (bool)           – when true, sum show_field across all matching treatments
                                           on the current local day (uses timezone_offset)
        """
        pill_configs = self.config.get('header_pills', [])

        # Clear all items from pills layout
        while self.pills_layout.count():
            item = self.pills_layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()

        if not pill_configs:
            self.header_pills_widget.hide()
            return

        pill_opacity_pct = self.config['appearance'].get('label_pill_opacity', 67)
        pill_alpha = round(pill_opacity_pct / 100 * 255)
        now_utc = datetime.utcnow()
        local_today = (now_utc + timedelta(hours=self.timezone_offset)).date()

        def _shadow():
            fx = QGraphicsDropShadowEffect()
            fx.setBlurRadius(6)
            fx.setOffset(0, 0)
            fx.setColor(QColor(0, 0, 0, 200))
            return fx

        def _parse_treatment_time(created_at):
            try:
                return datetime.strptime(created_at.split('.')[0] + 'Z', "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                try:
                    return datetime.strptime(created_at[:19], "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    return None

        pills_added = 0

        for pill_cfg in pill_configs:
            event_type_cfg = pill_cfg.get('event_type', '')
            if not event_type_cfg:
                continue

            label_text = pill_cfg.get('label', event_type_cfg)
            show_field = pill_cfg.get('show_field')
            suffix = pill_cfg.get('suffix', '')
            sum_daily = bool(pill_cfg.get('sum_daily', False))
            max_age_hours = float(pill_cfg.get('max_age_hours', 24))

            value_str = ''

            if sum_daily and show_field:
                # Sum show_field for all matching treatments on the current local day
                total = 0.0
                found_any = False
                for t in treatments:
                    if t.get('eventType', '').lower() != event_type_cfg.lower():
                        continue
                    t_time = _parse_treatment_time(t.get('created_at', ''))
                    if t_time is None:
                        continue
                    t_local_date = (t_time + timedelta(hours=self.timezone_offset)).date()
                    if t_local_date != local_today:
                        continue
                    raw_val = t.get(show_field)
                    try:
                        total += float(raw_val)
                        found_any = True
                    except (TypeError, ValueError):
                        pass
                if not found_any:
                    continue
                display_val = int(total) if total == int(total) else round(total, 1)
                value_str = f": {display_val}{suffix}"
            else:
                # Find most recent matching treatment within max_age_hours
                best_treatment = None
                best_time = None
                for t in treatments:
                    if t.get('eventType', '').lower() != event_type_cfg.lower():
                        continue
                    t_time = _parse_treatment_time(t.get('created_at', ''))
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
                if show_field:
                    val = best_treatment.get(show_field)
                    if val is not None:
                        value_str = f": {val}{suffix}"

            pill_text = f"{label_text}{value_str}"

            pill_label = QLabel(pill_text)
            pill_label.setFont(QFont("Segoe UI", self.config['age_font_size'] - 1, QFont.Weight.Normal))
            pill_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            pill_color = pill_cfg.get('color', '#80e8e0')
            pill_bg = f"rgba(0, 0, 0, {pill_alpha})"
            _pc = QColor(pill_color)
            border_rgba = f"rgba({_pc.red()}, {_pc.green()}, {_pc.blue()}, 80)"
            pill_label.setStyleSheet(
                f"color: {pill_color}; background-color: {pill_bg}; "
                f"border-radius: 6px; padding: 2px 10px; margin: 0px; "
                f"border: 1px solid {border_rgba};"
            )
            pill_label.setGraphicsEffect(_shadow())

            self.pills_layout.addWidget(pill_label)
            pills_added += 1

        if pills_added > 0:
            self.header_pills_widget.show()
        else:
            self.header_pills_widget.hide()

    def _apply_header_label_styles(self, glucose_text_color=None):
        """Style header labels with a dark pill background + drop-shadow for readability
        at any transparency level."""
        ui_colors = self.config['appearance']['colors']['ui']
        time_color = ui_colors['time_label']
        age_color = ui_colors['age_label']
        text_color = glucose_text_color or ui_colors['main_glucose_text']
        pill_opacity_pct = self.config['appearance'].get('label_pill_opacity', 67)
        pill_alpha = round(pill_opacity_pct / 100 * 255)
        pill = f"rgba(0, 0, 0, {pill_alpha})"

        def _shadow():
            fx = QGraphicsDropShadowEffect()
            fx.setBlurRadius(6)
            fx.setOffset(0, 0)
            fx.setColor(QColor(0, 0, 0, 200))
            return fx

        self.time_label.setStyleSheet(
            f"color: {time_color}; background-color: {pill}; "
            f"border-radius: 6px; padding: 2px 8px; margin: 0px; "
            f"border: 1px solid rgba(255, 255, 255, 18);"
        )
        self.time_label.setGraphicsEffect(_shadow())

        self.age_label.setStyleSheet(
            f"color: {age_color}; background-color: {pill}; "
            f"border-radius: 6px; padding: 2px 8px; margin: 0px; "
            f"border: 1px solid rgba(255, 255, 255, 18);"
        )
        self.age_label.setGraphicsEffect(_shadow())

        self.label.setStyleSheet(
            f"color: {text_color}; background-color: {pill}; "
            f"border-radius: 6px; padding: 3px 12px; font-weight: bold; "
            f"border: 1px solid rgba(255, 255, 255, 22);"
        )
        self.label.setGraphicsEffect(_shadow())

    def setup_graph(self):
        self.graph = pg.PlotWidget(axisItems={'bottom': TimeAxisItem(orientation='bottom')})
        self._update_graph_background()
        self.graph.setMinimumHeight(150)
        
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
        self.add_target_zones()
        self.current_time_line = None
        self.treatment_items = []

        # Intercept mouse events on the graph so border-drag resize still works.
        # PlotWidget is a QGraphicsView; the actual mouse events go to the viewport child,
        # so we must install the filter on both.
        self.graph.installEventFilter(self)
        _vp = self.graph.viewport()
        if _vp is not None:
            _vp.installEventFilter(self)
        
        graph_container = QWidget()
        graph_layout = QVBoxLayout()
        graph_layout.setContentsMargins(0, 2, 0, 0)
        graph_layout.setSpacing(0)
        graph_layout.addWidget(self.graph)
        graph_container.setLayout(graph_layout)
        graph_container.setStyleSheet("background: transparent;")
        self.main_layout.addWidget(graph_container, 1)

    def add_target_zones(self):
        """Add colored zones for low, target, and high glucose ranges"""
        target_low = self.config['target_low']
        target_high = self.config['target_high']
        
        low_zone = pg.LinearRegionItem(
            values=[40, target_low], 
            brush=pg.mkBrush(255, 0, 0, self.config['appearance']['target_zone_opacity']), 
            pen=pg.mkPen(None),
            movable=False
        )
        self.graph.addItem(low_zone)
        
        high_zone = pg.LinearRegionItem(
            values=[target_high, 300], 
            brush=pg.mkBrush(255, 165, 0, self.config['appearance']['target_zone_opacity']), 
            pen=pg.mkPen(None),
            movable=False
        )
        self.graph.addItem(high_zone)
        
        low_line_color = self.config['appearance']['colors']['target_zones']['low_line']
        high_line_color = self.config['appearance']['colors']['target_zones']['high_line']
        
        from PyQt6.QtGui import QColor
        low_color = QColor(low_line_color)
        low_color.setAlpha(120)
        high_color = QColor(high_line_color)
        high_color.setAlpha(120)
        
        self.graph.addLine(y=target_low, pen=pg.mkPen(low_color, width=2, style=Qt.PenStyle.DashLine))
        self.graph.addLine(y=target_high, pen=pg.mkPen(high_color, width=2, style=Qt.PenStyle.DashLine))

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
                    if edge:
                        self.setCursor(self.get_resize_cursor(edge))
                    else:
                        self.setCursor(Qt.CursorShape.ArrowCursor)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if self.resize_edge and event.button() == Qt.MouseButton.LeftButton:
                    self.resize_edge = None
                    self.resize_start_pos = None
                    self.resize_start_geometry = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
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
    
    def show_settings_dialog(self):
        dialog = SettingsDialog(self, self.config)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.update_glucose()

    def show_connection_dialog(self):
        """Re-run the setup wizard to change Nightscout URL / API secret."""
        wizard = SetupWizard()
        wizard.url_input.setText(self.nightscout_url)
        wizard.secret_input.setText(self.api_secret_raw)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self.nightscout_url, self.api_secret, self.api_secret_raw, new_config = load_config()
            self.config.update(new_config)
            self.timezone_offset = self.config['timezone_offset']
            self.update_glucose()
    
    def apply_settings(self, new_config):
        # Update connection credentials if they changed
        if new_config.get('nightscout_url'):
            if new_config['nightscout_url'] != self.nightscout_url:
                self._entries_cache = []       # new source → discard cached data
                self._treatments_cache = []
            self.nightscout_url = new_config['nightscout_url']
        if new_config.get('api_secret_raw'):
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
                "adaptive_dot_size": self.config.get('adaptive_dot_size', False),
                "data_point_size": self.config.get('data_point_size', 6),
                "gradient_interpolation": self.config.get('gradient_interpolation', True),
                "show_treatments": self.config.get('show_treatments', True),
                "treatments_to_fetch": self.config.get('treatments_to_fetch', 50),
                "header_pills": self.config.get('header_pills', []),
                "appearance": self.config.get('appearance', {})
            }
            
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            log.error("Error saving config: %s", e)
        
        self._apply_header_label_styles()
        
        self.label.setFont(QFont("Segoe UI", self.config['glucose_font_size'], QFont.Weight.Bold))
        self.time_label.setFont(QFont("Segoe UI", self.config['time_font_size'], QFont.Weight.Medium))
        self.age_label.setFont(QFont("Segoe UI", self.config['age_font_size'] - 1, QFont.Weight.Normal))

        self._update_graph_background()
        self._apply_widget_background()

        self.update_glucose()

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
            edge = self.get_resize_edge(event.position().toPoint())
            if edge:
                self.setCursor(self.get_resize_cursor(edge))
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
                
    def mouseReleaseEvent(self, event):
        """Handle mouse release events"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.resize_edge = None
            self.resize_start_pos = None
            self.resize_start_geometry = None
            self.old_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            
            if hasattr(self, 'position_save_timer'):
                self.position_save_timer.start()
        
    def moveEvent(self, event):
        """Called when window is moved, schedule position saving"""
        super().moveEvent(event)
        if hasattr(self, 'position_save_timer'):
            self.position_save_timer.start()
            
    def get_resize_edge(self, pos):
        """Determine which edge of the window is near the mouse position"""
        edge_margin = 12  # pixels from edge to trigger resize
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
        font = QFont("Arial", font_size, QFont.Weight.Bold)
        painter.setFont(font)
        luma = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
        text_color = QColor("#000000") if luma > 128 else QColor("#ffffff")
        painter.setPen(text_color)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)

        painter.end()
        return QIcon(pixmap)

    def _update_tray_icon(self, glucose, trend_arrow, delta_text):
        """Update tray icon colour and tooltip to reflect the latest glucose reading."""
        if not hasattr(self, '_tray'):
            return

        target_low = self.config['target_low']
        target_high = self.config['target_high']
        glucose_colors = self.config['appearance']['colors']['glucose_ranges']

        if glucose < target_low:
            bg_hex = glucose_colors['low']
        elif glucose <= target_high:
            bg_hex = glucose_colors['in_range']
        else:
            bg_hex = glucose_colors['high']

        self._tray.setIcon(self._make_tray_icon(glucose, bg_hex))

        age_text = self.age_label.text()
        tooltip = f"{glucose} {trend_arrow}"
        if delta_text:
            tooltip += f" {delta_text.strip()}"
        if age_text:
            tooltip += f"\n{age_text}"
        self._tray.setToolTip(f"NSOverlay\n{tooltip}")

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
        QApplication.quit()

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
            font = QFont("Segoe UI", self.config['glucose_font_size'], QFont.Weight.Bold)
            font_metrics = QFontMetrics(font)
            text_width = font_metrics.horizontalAdvance(glucose_text)
            padding = 40
            
            time_font = QFont("Segoe UI", self.config['time_font_size'], QFont.Weight.Medium)
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
    
    def fetch_all_treatments(self):
        """Fetch treatment data from Nightscout with minimal network traffic.

        First call: fetches the full ``treatments_to_fetch`` window.
        Subsequent calls: fetches 1 treatment to probe for new data.
          - If the latest ID is already cached → skip (no second request).
          - If a new ID is found → fetch 5 to catch up.
        Falls back to the in-memory cache on network errors.
        """
        try:
            headers = {"api-secret": self.api_secret}
            treatments_to_fetch = self.config.get('treatments_to_fetch', 50)

            if not self._treatments_cache:
                # First load — fetch the full history window
                url = f"{self.nightscout_url}/api/v1/treatments.json?count={treatments_to_fetch}"
                log.debug("[TREATMENTS] First load: %s", url)
                response = requests.get(url, headers=headers, timeout=5)
                log.debug("[TREATMENTS] HTTP %s", response.status_code)
                response.raise_for_status()
                new_treatments = response.json()
                log.debug("[TREATMENTS] Received %s treatments", len(new_treatments) if isinstance(new_treatments, list) else '?')
            else:
                # Probe: fetch only the single latest treatment
                probe_url = f"{self.nightscout_url}/api/v1/treatments.json?count=1"
                log.debug("[TREATMENTS] Probing: %s  (cache has %d items, last_id=%s)", probe_url, len(self._treatments_cache), self._treatments_cache[-1].get('_id'))
                probe_resp = requests.get(probe_url, headers=headers, timeout=5)
                log.debug("[TREATMENTS] Probe HTTP %s", probe_resp.status_code)
                probe_resp.raise_for_status()
                probe = probe_resp.json()

                if not isinstance(probe, list):
                    raise ValueError(f"Unexpected API response type: {type(probe).__name__}")

                probe_id = probe[0].get("_id") if probe else None
                cached_id = self._treatments_cache[-1].get("_id")
                log.debug("[TREATMENTS] Probe latest _id=%s  cached latest _id=%s", probe_id, cached_id)

                # No new treatment — return cache as-is
                if not probe or probe_id == cached_id:
                    log.debug("[TREATMENTS] No new treatment — returning cache")
                    return list(self._treatments_cache)

                # New treatment detected — fetch a small batch to catch up
                url = f"{self.nightscout_url}/api/v1/treatments.json?count=5"
                log.debug("[TREATMENTS] New treatment detected, fetching batch: %s", url)
                response = requests.get(url, headers=headers, timeout=5)
                log.debug("[TREATMENTS] Batch HTTP %s", response.status_code)
                response.raise_for_status()
                new_treatments = response.json()
                log.debug("[TREATMENTS] Batch received %s treatments", len(new_treatments) if isinstance(new_treatments, list) else '?')

            if not isinstance(new_treatments, list):
                raise ValueError(f"Unexpected API response type: {type(new_treatments).__name__}")

            if new_treatments:
                existing_ids = {t.get("_id") for t in self._treatments_cache}
                added = 0
                for t in new_treatments:
                    if isinstance(t, dict) and t.get("_id") not in existing_ids:
                        self._treatments_cache.append(t)
                        existing_ids.add(t.get("_id"))
                        added += 1
                # Sort oldest-first by created_at and trim to the configured window
                self._treatments_cache.sort(key=lambda t: t.get("created_at", ""))
                if len(self._treatments_cache) > treatments_to_fetch:
                    self._treatments_cache = self._treatments_cache[-treatments_to_fetch:]
                log.debug("[TREATMENTS] Added %d new items. Cache size: %d", added, len(self._treatments_cache))

            return list(self._treatments_cache)

        except Exception as e:
            log.error("[TREATMENTS] Error fetching treatments: %s", e)
            # Return whatever we have cached so the UI stays populated
            return list(self._treatments_cache)
    
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
        
        treatment_styles = {
            'insulin': {'color': '#1E90FF', 'symbol': '▼', 'size': 12},
            'carb': {'color': '#FFA500', 'symbol': '▲', 'size': 12},
            'exercise': {'color': '#9B59B6', 'symbol': '●', 'size': 10}
        }
        
        for treatment in treatments:
            try:
                created_at = treatment.get('created_at', treatment.get('timestamp'))
                if not created_at:
                    continue
                    
                if 'T' in created_at and 'Z' in created_at:
                    timestamp = datetime.strptime(created_at.split('.')[0] + 'Z', "%Y-%m-%dT%H:%M:%SZ")
                else:
                    timestamp = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ")
                
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
                    
                    outline_item = pg.TextItem(
                        text_content,
                        color='black',
                        anchor=(0.5, 1.0)
                    )
                    outline_item.setPos(unix_timestamp + 1, treatment_y_position - 1)
                    self.graph.addItem(outline_item)
                    self.treatment_items.append(outline_item)
                    
                    text_item = pg.TextItem(
                        text_content,
                        color=style['color'],
                        anchor=(0.5, 1.0)
                    )
                    text_item.setPos(unix_timestamp, treatment_y_position)
                    self.graph.addItem(text_item)
                    self.treatment_items.append(text_item)
                
                if carb_amount and carb_amount > 0:
                    style = treatment_styles['carb']
                    text_content = f"{style['symbol']}{int(carb_amount)}g"
                    
                    outline_item = pg.TextItem(
                        text_content,
                        color='black',
                        anchor=(0.5, 0.0)
                    )
                    outline_item.setPos(unix_timestamp + 1, treatment_y_position + 14)
                    self.graph.addItem(outline_item)
                    self.treatment_items.append(outline_item)
                    
                    text_item = pg.TextItem(
                        text_content,
                        color=style['color'],
                        anchor=(0.5, 0.0)
                    )
                    text_item.setPos(unix_timestamp, treatment_y_position + 15)
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
    
    def update_glucose(self):
        try:
            headers = {"api-secret": self.api_secret}
            
            if 'entries_to_fetch' in self.config:
                entries_to_fetch = int(self.config['entries_to_fetch'])
            else:
                time_window_hours = self.config.get('time_window_hours', 1)
                entries_to_fetch = max(int(90 * time_window_hours), 30)
            
            entries_to_fetch = min(entries_to_fetch, 288)

            # First load: fetch the full history window.
            # Subsequent polls: use the _id of the newest cached entry as a cursor so
            # the server returns ONLY documents created after it — no duplication, no
            # fixed count guess needed.
            last_date_ms = self._entries_cache[-1].get("date") if self._entries_cache else None
            if last_date_ms:
                url = (f"{self.nightscout_url}/api/v1/entries.json"
                       f"?find[date][$gt]={last_date_ms}&count=5")
            else:
                url = (f"{self.nightscout_url}/api/v1/entries.json"
                       f"?count={entries_to_fetch}")

            log.debug("[ENTRIES] Requesting: %s", url)
            log.debug("[ENTRIES] Cache before fetch: %d entries, last_date_ms=%s", len(self._entries_cache), last_date_ms)

            response = requests.get(url, headers=headers, timeout=5)

            log.debug("[ENTRIES] HTTP %s", response.status_code)
            response.raise_for_status()
            new_entries = response.json()
            if not isinstance(new_entries, list):
                raise ValueError(f"Unexpected API response type: {type(new_entries).__name__}")

            log.debug("[ENTRIES] Received %d new entries from server", len(new_entries))

            # Append truly-new entries (date cursor prevents dupes, but guard anyway)
            if new_entries:
                existing_keys = {e.get("_id") for e in self._entries_cache}
                added = 0
                for entry in new_entries:
                    if isinstance(entry, dict) and entry.get("_id") not in existing_keys:
                        self._entries_cache.append(entry)
                        existing_keys.add(entry.get("_id"))
                        added += 1
                self._entries_cache.sort(key=lambda e: e.get("date", 0))
                if len(self._entries_cache) > entries_to_fetch:
                    self._entries_cache = self._entries_cache[-entries_to_fetch:]
                log.debug("[ENTRIES] Added %d truly-new entries. Cache size: %d", added, len(self._entries_cache))
            else:
                log.debug("[ENTRIES] No new entries returned — cache unchanged (%d entries)", len(self._entries_cache))

            if self._entries_cache:
                latest = self._entries_cache[-1]
                log.debug("[ENTRIES] Latest cached: sgv=%s, dateString=%s, _id=%s", latest.get('sgv'), latest.get('dateString'), latest.get('_id'))

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
                # Parse timestamp — handle formats with and without milliseconds
                timestamp = None
                for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        timestamp = datetime.strptime(date_str[:26].rstrip('Z') + 'Z', fmt)
                        break
                    except ValueError:
                        try:
                            timestamp = datetime.strptime(date_str[:19], fmt[:19])
                            break
                        except ValueError:
                            continue
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

            self.graph.clear()
            self.treatment_items = []
            self.add_target_zones()
            
            current_now = datetime.now().timestamp()
            time_line_color = self.config['appearance']['colors']['graph']['current_time_line']
            if not hasattr(self, 'current_time_line') or self.current_time_line is None:
                self.current_time_line = pg.InfiniteLine(
                    pos=current_now,
                    angle=90,
                    pen=pg.mkPen(color=time_line_color, width=1, cosmetic=True),
                    movable=False
                )
            else:
                self.current_time_line.setPos(current_now)
            
            self.graph.addItem(self.current_time_line)
            self.current_time_line.setZValue(1000)
            
            glucose_colors = self.config['appearance']['colors']['glucose_ranges']
            target_low = self.config['target_low']
            target_high = self.config['target_high']
            
            for i in range(len(timestamps) - 1):
                x1, y1 = timestamps[i], glucose_values[i]
                x2, y2 = timestamps[i + 1], glucose_values[i + 1]
                
                avg_glucose = (y1 + y2) / 2
                segment_color = self.get_glucose_color_with_interpolation(
                    avg_glucose, target_low, target_high, glucose_colors,
                    sgv_max=sgv_max, sgv_min=sgv_min
                )
                
                _line_style_map = {
                    'solid': Qt.PenStyle.SolidLine,
                    'dash': Qt.PenStyle.DashLine,
                    'dot': Qt.PenStyle.DotLine,
                    'dashdot': Qt.PenStyle.DashDotLine,
                }
                _style_key = self.config['appearance'].get('graph_line_style', 'solid')
                _pen_style = _line_style_map.get(_style_key, Qt.PenStyle.SolidLine)
                self.graph.plot(
                    [x1, x2], [y1, y2],
                    pen=pg.mkPen(color=segment_color, width=self.config['appearance']['graph_line_width'], style=_pen_style),
                    antialias=True
                )
            
            dot_size = self.get_adaptive_dot_size()
            for i, (x, y, color) in enumerate(zip(timestamps, glucose_values, colors)):
                dot = self.graph.plot([x], [y],
                    pen=pg.mkPen(self.config['appearance']['marker_outline_color'], 
                                width=self.config['appearance']['marker_outline_width']),
                    symbol='o', 
                    symbolSize=dot_size,
                    symbolBrush=color,
                    symbolPen=pg.mkPen(self.config['appearance']['marker_outline_color'], 
                                      width=self.config['appearance']['marker_outline_width'])
                )
                dot.setZValue(100)
            
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
                
                text_item = pg.TextItem(
                    text=f"{newest_y}", 
                    color=newest_color,
                    fill=pg.mkBrush(0, 0, 0, 180),
                    border=pg.mkPen(newest_color, width=1),
                    anchor=(0.5, 0.5)
                )
                text_item.setFont(pg.QtGui.QFont("Arial", 10, pg.QtGui.QFont.Weight.Bold))
                text_item.setPos(text_x, text_y)
                self.graph.addItem(text_item)

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

            if glucose_values:
                y_min, y_max = self._compute_y_range(glucose_values)
                self.graph.setYRange(y_min, y_max, padding=0)  # type: ignore[call-arg]

            # Fetch all treatments once — used for both graph markers and header pills
            all_treatments = []
            if self.config.get('show_treatments', True) or self.config.get('header_pills', []):
                all_treatments = self.fetch_all_treatments()

            if self.config.get('show_treatments', True):
                self.add_treatments_to_graph(all_treatments)

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
            
            self.current_max_time = now

        except Exception as e:
            error_text = f"Error: {str(e)}"
            self.label.setText(error_text)
            self.time_label.setText("")
            self.age_label.setText("")
            if hasattr(self, '_tray'):
                self._tray.setToolTip(f"NSOverlay\nError: {str(e)}")
            self.auto_resize_to_fit_content(error_text)
            self._apply_widget_background()

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
            self.time_label.setText(current_time.strftime("%H:%M"))
            
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
                    
                self.age_label.setText(age_text)
                
                # Update age label color based on freshness
                if age_seconds <= 300:  # 5 minutes or less
                    age_color = "#00dd00"  # Fresh - green
                elif age_seconds <= 900:  # 15 minutes or less
                    age_color = "#dddd00"  # Warning - yellow
                else:
                    age_color = "#dd0000"  # Stale - red
                pill_opacity_pct = self.config['appearance'].get('label_pill_opacity', 67)
                pill_alpha = round(pill_opacity_pct / 100 * 255)
                pill = f"rgba(0, 0, 0, {pill_alpha})"
                self.age_label.setStyleSheet(
                    f"color: {age_color}; background-color: {pill}; "
                    f"border-radius: 4px; padding: 1px 6px; margin: 0px;"
                )
        except:
            pass  # Silent fail for time updates
    
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
            
            headers = {"api-secret": self.api_secret}
            response = requests.get(
                f"{self.nightscout_url}/api/v1/entries.json?count=50",
                headers=headers,
                timeout=3
            )
            
            if response.status_code == 200:
                entries = response.json()
                if entries:
                    glucose_values = [entry["sgv"] for entry in entries if isinstance(entry.get("sgv"), (int, float))]
                    if glucose_values:
                        return self._compute_y_range(glucose_values)
        except:
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
        self.save_zoom_state()
    
    def get_adaptive_dot_size(self):
        """Calculate adaptive dot size based on Y-axis zoom level"""
        if not self.config.get('adaptive_dot_size', True):
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

        self._apply_widget_background()
        self._apply_header_label_styles(glucose_text_color=text_color)


class ColorButton(QPushButton):
    """Push-button that shows a color swatch and opens QColorDialog on click."""
    colorChanged = pyqtSignal(str)

    def __init__(self, color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(80, 24)
        self._refresh()
        self.clicked.connect(self._pick)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Choose Color")
        if c.isValid():
            self._color = c.name()
            self._refresh()
            self.colorChanged.emit(self._color)

    def _refresh(self):
        r, g, b = int(self._color[1:3], 16), int(self._color[3:5], 16), int(self._color[5:7], 16)
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        text = "#000000" if luma > 128 else "#ffffff"
        self.setStyleSheet(
            f"background:{self._color}; color:{text}; "
            f"border:1px solid #666; border-radius:3px; font-size:10px;"
        )
        self.setText(self._color)

    @property
    def color(self) -> str:
        return self._color

    @color.setter
    def color(self, val: str):
        self._color = val
        self._refresh()


class PillEditDialog(QDialog):
    """Sub-dialog for adding or editing a single header pill entry."""

    _FIELD_OPTIONS = ["notes", "amount", "insulin", "carbs", "duration",
                      "absolute", "rate", "enteredBy", "eventType"]

    def __init__(self, parent, pill=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Pill" if pill else "Add Pill")
        self.setModal(True)
        self.setFixedSize(440, 320)
        pill = pill or {}

        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.event_type_edit = QLineEdit(pill.get("event_type", ""))
        self.event_type_edit.setPlaceholderText("e.g. Basal Injection")
        form.addRow("Event Type:", self.event_type_edit)

        self.label_edit = QLineEdit(pill.get("label", ""))
        self.label_edit.setPlaceholderText("e.g. Basal")
        form.addRow("Label:", self.label_edit)

        self.field_combo = QComboBox()
        self.field_combo.addItems(self._FIELD_OPTIONS)
        cur = pill.get("show_field", "notes")
        idx = self.field_combo.findText(cur)
        self.field_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Show Field:", self.field_combo)

        self.suffix_edit = QLineEdit(pill.get("suffix", ""))
        self.suffix_edit.setPlaceholderText("e.g. U")
        form.addRow("Suffix:", self.suffix_edit)

        self.color_btn = ColorButton(pill.get("color", "#80e8e0"))
        form.addRow("Color:", self.color_btn)

        self.sum_daily_chk = QCheckBox("Sum all matching entries for today")
        self.sum_daily_chk.setChecked(bool(pill.get("sum_daily", False)))
        form.addRow("", self.sum_daily_chk)

        self.max_age_spin = QDoubleSpinBox()
        self.max_age_spin.setRange(0, 72)
        self.max_age_spin.setDecimals(1)
        self.max_age_spin.setSuffix(" h")
        self.max_age_spin.setSpecialValueText("—")
        self.max_age_spin.setValue(float(pill.get("max_age_hours", 0)))
        form.addRow("Max Age:", self.max_age_spin)

        layout.addLayout(form)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.setMinimumWidth(80)
        ok_btn.setMinimumHeight(32)
        ok_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(80)
        cancel_btn.setMinimumHeight(32)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)
        self.setStyleSheet(DARK_QSS)

    def _accept(self):
        if not self.event_type_edit.text().strip():
            self.event_type_edit.setFocus()
            return
        self.accept()

    def value(self) -> dict:
        d = {
            "event_type": self.event_type_edit.text().strip(),
            "label":      self.label_edit.text().strip(),
            "show_field": self.field_combo.currentText(),
            "suffix":     self.suffix_edit.text(),
            "color":      self.color_btn.color,
            "sum_daily":  self.sum_daily_chk.isChecked(),
        }
        if self.max_age_spin.value() > 0:
            d["max_age_hours"] = self.max_age_spin.value()
        return d


class SettingsDialog(QDialog):
    """Tabbed settings dialog covering all config.json parameters."""

    def __init__(self, parent, config: dict):
        super().__init__(parent)
        self.parent_widget = parent
        import copy
        self.config = copy.deepcopy(config)
        self._pills: list = list(self.config.get("header_pills", []))

        self.setWindowTitle("Settings — NSOverlay")
        self.setModal(True)
        self.setMinimumSize(520, 600)

        root = QVBoxLayout()
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_connection_tab(), "Connection")
        self.tabs.addTab(self._build_graph_tab(),      "Graph")
        self.tabs.addTab(self._build_appearance_tab(), "Appearance")
        self.tabs.addTab(self._build_colors_tab(),     "Colors")
        self.tabs.addTab(self._build_pills_tab(),      "Pills")
        root.addWidget(self.tabs)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._apply)
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._ok)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        for b in (apply_btn, ok_btn, cancel_btn):
            b.setMinimumWidth(88)
            b.setMinimumHeight(32)
            btn_row.addWidget(b)
        root.addLayout(btn_row)

        self.setLayout(root)
        self.setStyleSheet(DARK_QSS)
    # ── tab builders ──────────────────────────────────────────────────────────

    def _build_connection_tab(self) -> QWidget:
        c = self.config
        w, form = self._form_widget()

        self.url_edit = QLineEdit(self.parent_widget.nightscout_url)
        self.url_edit.setPlaceholderText("https://your-site.fly.dev")
        form.addRow("Nightscout URL:", self.url_edit)

        self.secret_edit = QLineEdit(self.parent_widget.api_secret_raw)
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_edit.setPlaceholderText("API secret (plain text)")
        form.addRow("API Secret:", self.secret_edit)

        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(5000, 600_000)
        self.refresh_spin.setSingleStep(5000)
        self.refresh_spin.setSuffix(" ms")
        self.refresh_spin.setValue(c.get("refresh_interval", 60000))
        form.addRow("Refresh Interval:", self.refresh_spin)

        self.tz_spin = QDoubleSpinBox()
        self.tz_spin.setRange(-14, 14)
        self.tz_spin.setDecimals(1)
        self.tz_spin.setSuffix(" h")
        self.tz_spin.setValue(c.get("timezone_offset", 0))
        form.addRow("Timezone Offset:", self.tz_spin)

        self.entries_spin = QSpinBox()
        self.entries_spin.setRange(10, 500)
        self.entries_spin.setValue(c.get("entries_to_fetch", 90))
        form.addRow("Entries to Fetch:", self.entries_spin)

        self.time_window_spin = QDoubleSpinBox()
        self.time_window_spin.setRange(0.25, 24)
        self.time_window_spin.setDecimals(2)
        self.time_window_spin.setSuffix(" h")
        self.time_window_spin.setValue(c.get("time_window_hours", 3))
        form.addRow("Time Window:", self.time_window_spin)

        return w

    def _build_graph_tab(self) -> QWidget:
        c = self.config
        ap = c.get("appearance", {})
        w, form = self._form_widget()

        self.target_low_spin = QSpinBox()
        self.target_low_spin.setRange(40, 120)
        self.target_low_spin.setSuffix(" mg/dL")
        self.target_low_spin.setValue(c.get("target_low", 70))
        form.addRow("Low Target:", self.target_low_spin)

        self.target_high_spin = QSpinBox()
        self.target_high_spin.setRange(121, 400)
        self.target_high_spin.setSuffix(" mg/dL")
        self.target_high_spin.setValue(c.get("target_high", 180))
        form.addRow("High Target:", self.target_high_spin)

        self.line_style_combo = QComboBox()
        for display, data in (("Solid", "solid"), ("Dash", "dash"),
                               ("Dot", "dot"), ("Dash-Dot", "dashdot")):
            self.line_style_combo.addItem(display, data)
        cur = ap.get("graph_line_style", "solid")
        self.line_style_combo.setCurrentIndex(
            max(0, self.line_style_combo.findData(cur)))
        form.addRow("Line Style:", self.line_style_combo)

        self.line_width_slider, line_row = self._slider_row(1, 8, ap.get("graph_line_width", 2))
        form.addRow("Line Width:", line_row)

        self.dot_size_slider, dot_row = self._slider_row(2, 20, c.get("data_point_size", 6))
        form.addRow("Dot Size:", dot_row)

        self.adaptive_dot_chk = QCheckBox()
        self.adaptive_dot_chk.setChecked(bool(c.get("adaptive_dot_size", False)))
        form.addRow("Adaptive Dot Size:", self.adaptive_dot_chk)

        self.show_delta_chk = QCheckBox()
        self.show_delta_chk.setChecked(bool(c.get("show_delta", True)))
        form.addRow("Show Delta:", self.show_delta_chk)

        self.show_y_label_chk = QCheckBox()
        self.show_y_label_chk.setChecked(bool(ap.get("show_y_label", True)))
        form.addRow("Show Y Label:", self.show_y_label_chk)

        self.gradient_chk = QCheckBox()
        self.gradient_chk.setChecked(bool(c.get("gradient_interpolation", True)))
        form.addRow("Color Gradient:", self.gradient_chk)

        return w

    def _build_appearance_tab(self) -> QWidget:
        c = self.config
        ap = c.get("appearance", {})
        w, form = self._form_widget()

        self.glucose_font_spin = QSpinBox()
        self.glucose_font_spin.setRange(8, 48)
        self.glucose_font_spin.setSuffix(" pt")
        self.glucose_font_spin.setValue(c.get("glucose_font_size", 18))
        form.addRow("Glucose Font:", self.glucose_font_spin)

        self.time_font_spin = QSpinBox()
        self.time_font_spin.setRange(6, 32)
        self.time_font_spin.setSuffix(" pt")
        self.time_font_spin.setValue(c.get("time_font_size", 12))
        form.addRow("Time Font:", self.time_font_spin)

        self.age_font_spin = QSpinBox()
        self.age_font_spin.setRange(6, 24)
        self.age_font_spin.setSuffix(" pt")
        self.age_font_spin.setValue(c.get("age_font_size", 10))
        form.addRow("Age Font:", self.age_font_spin)

        self.graph_opacity_slider, graph_op_row = self._pct_slider_row(
            ap.get("graph_background_opacity", 100))
        self.graph_opacity_slider.valueChanged.connect(self._preview_graph_opacity)
        self.graph_opacity_slider.sliderReleased.connect(
            lambda: self.parent_widget.apply_settings(self._collect()))
        form.addRow("Graph Opacity:", graph_op_row)

        self.pill_opacity_slider, pill_op_row = self._pct_slider_row(
            ap.get("label_pill_opacity", 67))
        self.pill_opacity_slider.valueChanged.connect(self._preview_pill_opacity)
        self.pill_opacity_slider.sliderReleased.connect(
            lambda: self.parent_widget.apply_settings(self._collect()))
        form.addRow("Pill Opacity:", pill_op_row)

        self.zone_opacity_spin = QSpinBox()
        self.zone_opacity_spin.setRange(0, 255)
        self.zone_opacity_spin.setToolTip("Alpha value 0-255 used for target zone fill color")
        self.zone_opacity_spin.setValue(ap.get("target_zone_opacity", 20))
        form.addRow("Zone Fill Alpha:", self.zone_opacity_spin)

        self.grid_opacity_slider, grid_op_row = self._pct_slider_row(
            int(ap.get("grid_opacity", 0.3) * 100))
        form.addRow("Grid Opacity:", grid_op_row)

        self.outline_width_spin = QDoubleSpinBox()
        self.outline_width_spin.setRange(0, 5)
        self.outline_width_spin.setDecimals(1)
        self.outline_width_spin.setSingleStep(0.5)
        self.outline_width_spin.setValue(ap.get("marker_outline_width", 1.5))
        form.addRow("Dot Outline Width:", self.outline_width_spin)

        return w

    def _build_colors_tab(self) -> QWidget:
        ap = self.config.get("appearance", {})
        colors = ap.get("colors", {})
        ui_c    = colors.get("ui", {})
        graph_c = colors.get("graph", {})
        gr_c    = colors.get("glucose_ranges", {})
        tz_c    = colors.get("target_zones", {})

        inner = QWidget()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)
        form.setContentsMargins(16, 12, 16, 12)

        def _sep(title):
            lbl = QLabel(f"  {title}")
            lbl.setStyleSheet(
                "color: #80e8e0; font-weight: bold; font-size: 10px; "
                "background: #22223a; padding: 4px 10px; border-radius: 4px; "
                "margin-top: 4px; border-left: 3px solid #00d4aa;")
            form.addRow(lbl)

        _sep("Glucose Ranges")
        self.c_low     = ColorButton(gr_c.get("low", "#ff4444"))
        self.c_inrange = ColorButton(gr_c.get("in_range", "#00d4aa"))
        self.c_high    = ColorButton(gr_c.get("high", "#ff8800"))
        form.addRow("Low:", self.c_low)
        form.addRow("In Range:", self.c_inrange)
        form.addRow("High:", self.c_high)

        _sep("Target Zone Lines")
        self.c_low_line  = ColorButton(tz_c.get("low_line", "#ff4444"))
        self.c_high_line = ColorButton(tz_c.get("high_line", "#ff8800"))
        form.addRow("Low Line:", self.c_low_line)
        form.addRow("High Line:", self.c_high_line)

        _sep("Graph")
        self.c_graph_bg   = ColorButton(graph_c.get("background", "#1a1a1a"))
        self.c_main_line  = ColorButton(graph_c.get("main_line", "#a0a0a0"))
        self.c_axis       = ColorButton(graph_c.get("axis_lines", "#888888"))
        self.c_axis_text  = ColorButton(graph_c.get("axis_text", "#cccccc"))
        self.c_time_line  = ColorButton(graph_c.get("current_time_line", "#888888"))
        form.addRow("Background:", self.c_graph_bg)
        form.addRow("Main Line:", self.c_main_line)
        form.addRow("Axis Lines:", self.c_axis)
        form.addRow("Axis Text:", self.c_axis_text)
        form.addRow("Current Time Line:", self.c_time_line)

        _sep("UI Labels")
        self.c_glucose_text = ColorButton(ui_c.get("main_glucose_text", "#ffffff"))
        self.c_time_label   = ColorButton(ui_c.get("time_label", "#cccccc"))
        self.c_age_label    = ColorButton(ui_c.get("age_label", "#999999"))
        self.c_widget_bg    = ColorButton(ui_c.get("widget_background", "#2a2a2a"))
        form.addRow("Glucose Text:", self.c_glucose_text)
        form.addRow("Time Label:", self.c_time_label)
        form.addRow("Age Label:", self.c_age_label)
        form.addRow("Widget Background:", self.c_widget_bg)

        inner.setLayout(form)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)

        w = QWidget()
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        w.setLayout(outer)
        return w

    def _build_pills_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info = QLabel(
            "Pills appear in the header row and show Nightscout treatment data. "
            "Double-click an entry to edit it.")
        info.setStyleSheet("color: #7070a8; font-size: 10px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.pills_list = QListWidget()
        self.pills_list.setAlternatingRowColors(True)
        self.pills_list.itemDoubleClicked.connect(self._edit_pill)
        self._refresh_pills_list()
        layout.addWidget(self.pills_list)

        btn_row = QHBoxLayout()
        add_btn    = QPushButton("Add")
        edit_btn   = QPushButton("Edit")
        remove_btn = QPushButton("Remove")
        up_btn     = QPushButton("↑")
        dn_btn     = QPushButton("↓")
        up_btn.setFixedWidth(32)
        dn_btn.setFixedWidth(32)
        add_btn.clicked.connect(self._add_pill)
        edit_btn.clicked.connect(self._edit_pill)
        remove_btn.clicked.connect(self._remove_pill)
        up_btn.clicked.connect(self._move_pill_up)
        dn_btn.clicked.connect(self._move_pill_down)
        for b in (add_btn, edit_btn, remove_btn, up_btn, dn_btn):
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        w.setLayout(layout)
        return w

    # ── pills helpers ─────────────────────────────────────────────────────────

    def _refresh_pills_list(self):
        self.pills_list.clear()
        for p in self._pills:
            label  = p.get("label", p.get("event_type", "?"))
            field  = p.get("show_field", "")
            suffix = p.get("suffix", "")
            mode   = "daily sum" if p.get("sum_daily") else f"max {p.get('max_age_hours', '—')}h"
            self.pills_list.addItem(f"{label}  ·  {field} {suffix}  [{mode}]")

    def _add_pill(self):
        dlg = PillEditDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._pills.append(dlg.value())
            self._refresh_pills_list()
            self.pills_list.setCurrentRow(len(self._pills) - 1)

    def _edit_pill(self, *_):
        row = self.pills_list.currentRow()
        if row < 0:
            return
        dlg = PillEditDialog(self, self._pills[row])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._pills[row] = dlg.value()
            self._refresh_pills_list()
            self.pills_list.setCurrentRow(row)

    def _remove_pill(self):
        row = self.pills_list.currentRow()
        if row < 0:
            return
        self._pills.pop(row)
        self._refresh_pills_list()

    def _move_pill_up(self):
        row = self.pills_list.currentRow()
        if row > 0:
            self._pills[row - 1], self._pills[row] = self._pills[row], self._pills[row - 1]
            self._refresh_pills_list()
            self.pills_list.setCurrentRow(row - 1)

    def _move_pill_down(self):
        row = self.pills_list.currentRow()
        if 0 <= row < len(self._pills) - 1:
            self._pills[row + 1], self._pills[row] = self._pills[row], self._pills[row + 1]
            self._refresh_pills_list()
            self.pills_list.setCurrentRow(row + 1)

    # ── widget factory helpers ────────────────────────────────────────────────

    @staticmethod
    def _form_widget():
        """Return (QWidget, QFormLayout) ready to add rows to."""
        w = QWidget()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)
        w.setLayout(form)
        return w, form

    @staticmethod
    def _slider_row(lo: int, hi: int, val: int):
        """Return (QSlider, container_widget) with a live value label."""
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(val)
        lbl = QLabel(str(val))
        lbl.setFixedWidth(28)
        slider.valueChanged.connect(lambda v: lbl.setText(str(v)))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(slider)
        row.addWidget(lbl)
        container = QWidget()
        container.setLayout(row)
        return slider, container

    @staticmethod
    def _pct_slider_row(val: int):
        """Return (QSlider, container_widget) with a live % label."""
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(int(val))
        lbl = QLabel(f"{int(val)}%")
        lbl.setFixedWidth(36)
        slider.valueChanged.connect(lambda v: lbl.setText(f"{v}%"))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(slider)
        row.addWidget(lbl)
        container = QWidget()
        container.setLayout(row)
        return slider, container

    # ── live preview helpers ──────────────────────────────────────────────────

    def _preview_graph_opacity(self, v: int):
        self.parent_widget.config.setdefault("appearance", {})["graph_background_opacity"] = v
        self.parent_widget._update_graph_background()
        self.parent_widget._apply_widget_background()

    def _preview_pill_opacity(self, v: int):
        self.parent_widget.config.setdefault("appearance", {})["label_pill_opacity"] = v
        self.parent_widget._apply_header_label_styles()

    # ── collect / apply ───────────────────────────────────────────────────────

    def _collect(self) -> dict:
        """Read every widget and return a complete config dict."""
        c  = self.config
        ap = dict(c.get("appearance", {}))

        # Graph appearance
        ap["graph_line_width"]        = self.line_width_slider.value()
        ap["graph_line_style"]        = self.line_style_combo.currentData()
        ap["graph_background_opacity"]= self.graph_opacity_slider.value()
        ap["label_pill_opacity"]      = self.pill_opacity_slider.value()
        ap["target_zone_opacity"]     = self.zone_opacity_spin.value()
        ap["grid_opacity"]            = round(self.grid_opacity_slider.value() / 100, 2)
        ap["marker_outline_width"]    = self.outline_width_spin.value()
        ap["show_y_label"]            = self.show_y_label_chk.isChecked()

        # Colors
        orig_colors = c.get("appearance", {}).get("colors", {})
        orig_ui     = orig_colors.get("ui", {})
        orig_tz     = orig_colors.get("target_zones", {})
        ap["colors"] = {
            "glucose_ranges": {
                "low":      self.c_low.color,
                "in_range": self.c_inrange.color,
                "high":     self.c_high.color,
            },
            "target_zones": {
                "low_line":    self.c_low_line.color,
                "high_line":   self.c_high_line.color,
                "target_fill": orig_tz.get("target_fill", "#00d4aa"),
            },
            "graph": {
                "background":        self.c_graph_bg.color,
                "main_line":         self.c_main_line.color,
                "axis_lines":        self.c_axis.color,
                "axis_text":         self.c_axis_text.color,
                "axis_labels":       self.c_axis_text.color,
                "current_time_line": self.c_time_line.color,
            },
            "ui": {
                "main_glucose_text":             self.c_glucose_text.color,
                "time_label":                    self.c_time_label.color,
                "age_label":                     self.c_age_label.color,
                "widget_background":             self.c_widget_bg.color,
                "close_button":                  orig_ui.get("close_button", "#ff4444"),
                "close_button_hover":            orig_ui.get("close_button_hover", "#ff6666"),
                "close_button_background":       orig_ui.get("close_button_background", "rgba(0,0,0,150)"),
                "close_button_hover_background": orig_ui.get("close_button_hover_background", "rgba(255,68,68,200)"),
            },
        }

        new_config = dict(c)
        new_config["nightscout_url"]        = self.url_edit.text().strip().rstrip("/")
        new_config["api_secret_raw"]        = self.secret_edit.text().strip()
        new_config["refresh_interval"]      = self.refresh_spin.value()
        new_config["timezone_offset"]       = self.tz_spin.value()
        new_config["time_window_hours"]     = self.time_window_spin.value()
        new_config["entries_to_fetch"]      = self.entries_spin.value()
        new_config["target_low"]            = self.target_low_spin.value()
        new_config["target_high"]           = self.target_high_spin.value()
        new_config["data_point_size"]       = self.dot_size_slider.value()
        new_config["adaptive_dot_size"]     = self.adaptive_dot_chk.isChecked()
        new_config["show_delta"]            = self.show_delta_chk.isChecked()
        new_config["gradient_interpolation"]= self.gradient_chk.isChecked()
        new_config["glucose_font_size"]     = self.glucose_font_spin.value()
        new_config["time_font_size"]        = self.time_font_spin.value()
        new_config["age_font_size"]         = self.age_font_spin.value()
        new_config["header_pills"]          = list(self._pills)
        new_config["appearance"]            = ap
        return new_config

    def _apply(self):
        self.parent_widget.apply_settings(self._collect())

    def _ok(self):
        self._apply()
        self.accept()


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
        wizard = SetupWizard()
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
