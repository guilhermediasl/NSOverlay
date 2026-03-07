import sys
import os
import json
import hashlib
import requests
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QMenu,
                             QSlider, QSpinBox, QPushButton, QDialog, QFormLayout, QGroupBox,
                             QLineEdit)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import QFont, QColor, QAction, QFontMetrics
from PyQt6.QtWidgets import QGraphicsDropShadowEffect
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
    
    # Deep-merge appearance so partial configs still work
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
        'refresh_interval': max(5000, int(config.get("refresh_interval_ms", 60000))),
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
        subtitle.setStyleSheet("color: #888888;")
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
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(32)
        cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save && Start")
        self.save_btn.setMinimumHeight(32)
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

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
            "refresh_interval_ms": 60000,
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
        self.pills_layout.setSpacing(6)
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
                color: rgba(255, 255, 255, 60);
                background-color: rgba(40, 40, 40, 80);
                border-radius: 15px;
                padding: 3px;
                margin: 0px;
            }}
            QLabel:hover {{
                background-color: rgba(200, 40, 40, 255);
                color: {close_button_hover};
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

            pill = f"rgba(0, 0, 0, {pill_alpha})"
            pill_label.setStyleSheet(
                f"color: #80e8e0; background-color: {pill}; "
                f"border-radius: 4px; padding: 1px 8px; margin: 0px;"
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
            f"border-radius: 4px; padding: 1px 6px; margin: 0px;"
        )
        self.time_label.setGraphicsEffect(_shadow())

        self.age_label.setStyleSheet(
            f"color: {age_color}; background-color: {pill}; "
            f"border-radius: 4px; padding: 1px 6px; margin: 0px;"
        )
        self.age_label.setGraphicsEffect(_shadow())

        self.label.setStyleSheet(
            f"color: {text_color}; background-color: {pill}; "
            f"border-radius: 4px; padding: 2px 10px; font-weight: bold; border: none;"
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
        
        close_action = QAction("Close", self)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)
        
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
        self.config.update(new_config)
        
        try:
            import copy
            config_to_save = copy.deepcopy(self.config)
            config_data = {
                "nightscout_url": self.nightscout_url,
                "api_secret": self.api_secret_raw,
                "refresh_interval_ms": self.config.get('refresh_interval', 60000),
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
                "appearance": self.config.get('appearance', {})
            }
            
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")
        
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
        edge_margin = 8  # pixels from edge to trigger resize
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
        event.accept()

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
            print(f"Warning: Could not save position and size: {e}")
    
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
                print(f"Error loading position/size: {e}")
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
            print(f"Auto-resize failed: {e}")
    
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
        print(f"Gradient interpolation: {status}")
        
        original_title = self.windowTitle()
        self.setWindowTitle(f"Gradient Interpolation: {status}")
        QTimer.singleShot(2000, lambda: self.setWindowTitle(original_title))
    
    def reload_config(self):
        """Reload configuration from file in real-time"""
        try:
            old_config = self.config.copy()
            self.nightscout_url, self.api_secret, self.api_secret_raw, new_config = load_config()
            self.config = new_config
            self.timezone_offset = self.config['timezone_offset']
            
            if old_config['refresh_interval'] != self.config['refresh_interval']:
                self.timer.setInterval(self.config['refresh_interval'])
            
            self.update_glucose()
            print("Configuration reloaded successfully")
        except Exception as e:
            print(f"Error reloading config: {e}")
    
    def save_config_setting(self, key, value):
        """Save a single setting to the config file"""
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            config_data[key] = value
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Error saving config setting '{key}': {e}")

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
        """Fetch all treatment data from Nightscout (unfiltered)."""
        try:
            headers = {"api-secret": self.api_secret}
            treatments_to_fetch = self.config.get('treatments_to_fetch', 50)

            response = requests.get(
                f"{self.nightscout_url}/api/v1/treatments.json?count={treatments_to_fetch}",
                headers=headers,
                timeout=5
            )

            response.raise_for_status()
            return response.json()

        except Exception as e:
            print(f"Error fetching treatments: {e}")
            return []
    
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
                print(f"Error adding treatment marker: {e}")
                continue
    
    def update_glucose(self):
        try:
            headers = {"api-secret": self.api_secret}
            
            if 'entries_to_fetch' in self.config:
                entries_to_fetch = self.config['entries_to_fetch']
            else:
                time_window_hours = self.config.get('time_window_hours', 1)
                entries_to_fetch = max(90 * time_window_hours, 30)
            
            entries_to_fetch = min(entries_to_fetch, 288)

            response = requests.get(
                f"{self.nightscout_url}/api/v1/entries.json?count={entries_to_fetch}",
                headers=headers,
                timeout=5
            )

            response.raise_for_status()
            entries = response.json()
            if not isinstance(entries, list):
                raise ValueError(f"Unexpected API response type: {type(entries).__name__}")
            entries.reverse()

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
                    print(f"Warning: could not parse dateString {date_str!r}, skipping entry")
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


class SettingsDialog(QDialog):
    
    def __init__(self, parent, config):
        super().__init__(parent)
        self.parent_widget = parent
        self.config = config.copy()
        self.setWindowTitle("Appearance Settings")
        self.setModal(True)
        self.setFixedSize(400, 500)
        
        layout = QVBoxLayout()
        
        visual_group = QGroupBox("Visual Settings")
        visual_form = QFormLayout()
        
        self.dot_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.dot_size_slider.setRange(3, 15)
        self.dot_size_slider.setValue(self.config.get('data_point_size', 6))
        self.dot_size_value = QLabel(str(self.dot_size_slider.value()))
        self.dot_size_slider.valueChanged.connect(lambda v: self.dot_size_value.setText(str(v)))
        
        dot_layout = QHBoxLayout()
        dot_layout.addWidget(self.dot_size_slider)
        dot_layout.addWidget(self.dot_size_value)
        visual_form.addRow("Dot Size:", dot_layout)
        
        self.line_width_slider = QSlider(Qt.Orientation.Horizontal)
        self.line_width_slider.setRange(1, 8)
        self.line_width_slider.setValue(self.config.get('appearance', {}).get('graph_line_width', 4))
        self.line_width_value = QLabel(str(self.line_width_slider.value()))
        self.line_width_slider.valueChanged.connect(lambda v: self.line_width_value.setText(str(v)))
        
        line_layout = QHBoxLayout()
        line_layout.addWidget(self.line_width_slider)
        line_layout.addWidget(self.line_width_value)
        visual_form.addRow("Line Width:", line_layout)

        # Graph background opacity (0 = fully transparent, 100 = fully opaque)
        self.graph_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.graph_opacity_slider.setRange(0, 100)
        current_opacity = self.config.get('appearance', {}).get('graph_background_opacity', 100)
        self.graph_opacity_slider.setValue(current_opacity)
        self.graph_opacity_value = QLabel(f"{current_opacity}%")
        self.graph_opacity_slider.valueChanged.connect(self._on_opacity_preview)
        self.graph_opacity_slider.sliderReleased.connect(self._on_opacity_released)
        
        opacity_layout = QHBoxLayout()
        opacity_layout.addWidget(self.graph_opacity_slider)
        opacity_layout.addWidget(self.graph_opacity_value)
        visual_form.addRow("Graph Opacity:", opacity_layout)

        # Label pill opacity (0 = invisible, 100 = fully opaque)
        self.pill_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.pill_opacity_slider.setRange(0, 100)
        current_pill = self.config.get('appearance', {}).get('label_pill_opacity', 67)
        self.pill_opacity_slider.setValue(current_pill)
        self.pill_opacity_value = QLabel(f"{current_pill}%")
        self.pill_opacity_slider.valueChanged.connect(self._on_pill_opacity_preview)
        self.pill_opacity_slider.sliderReleased.connect(self._on_pill_opacity_released)

        pill_layout = QHBoxLayout()
        pill_layout.addWidget(self.pill_opacity_slider)
        pill_layout.addWidget(self.pill_opacity_value)
        visual_form.addRow("Label Pill Opacity:", pill_layout)

        visual_group.setLayout(visual_form)
        layout.addWidget(visual_group)
        
        # Font Settings Group
        font_group = QGroupBox("Font Sizes")
        font_form = QFormLayout()
        
        self.glucose_font_spin = QSpinBox()
        self.glucose_font_spin.setRange(12, 32)
        self.glucose_font_spin.setValue(self.config.get('glucose_font_size', 18))
        font_form.addRow("Main Glucose:", self.glucose_font_spin)
        
        self.time_font_spin = QSpinBox()
        self.time_font_spin.setRange(8, 20)
        self.time_font_spin.setValue(self.config.get('time_font_size', 12))
        font_form.addRow("Time Display:", self.time_font_spin)
        
        self.age_font_spin = QSpinBox()
        self.age_font_spin.setRange(6, 16)
        self.age_font_spin.setValue(self.config.get('age_font_size', 10))
        font_form.addRow("Age Display:", self.age_font_spin)
        
        font_group.setLayout(font_form)
        layout.addWidget(font_group)
        
        # Target Range Group
        target_group = QGroupBox("Target Range")
        target_form = QFormLayout()
        
        self.target_low_spin = QSpinBox()
        self.target_low_spin.setRange(40, 120)
        self.target_low_spin.setValue(self.config.get('target_low', 70))
        self.target_low_spin.setSuffix(" mg/dL")
        target_form.addRow("Low Target:", self.target_low_spin)
        
        self.target_high_spin = QSpinBox()
        self.target_high_spin.setRange(120, 300)
        self.target_high_spin.setValue(self.config.get('target_high', 180))
        self.target_high_spin.setSuffix(" mg/dL")
        target_form.addRow("High Target:", self.target_high_spin)
        
        target_group.setLayout(target_form)
        layout.addWidget(target_group)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.apply_settings)
        
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept_settings)
        
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        
        button_layout.addWidget(apply_button)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def get_current_settings(self):
        settings = self.config.copy()
        
        settings['data_point_size'] = self.dot_size_slider.value()
        settings['glucose_font_size'] = self.glucose_font_spin.value()
        settings['time_font_size'] = self.time_font_spin.value()
        settings['age_font_size'] = self.age_font_spin.value()
        settings['target_low'] = self.target_low_spin.value()
        settings['target_high'] = self.target_high_spin.value()
        
        if 'appearance' not in settings:
            settings['appearance'] = {}
        settings['appearance']['graph_line_width'] = self.line_width_slider.value()
        settings['appearance']['graph_background_opacity'] = self.graph_opacity_slider.value()
        settings['appearance']['label_pill_opacity'] = self.pill_opacity_slider.value()
        
        return settings
    
    def _on_opacity_preview(self, value):
        """Update label and repaint backgrounds instantly while dragging — no data refresh."""
        self.graph_opacity_value.setText(f"{value}%")
        # Write directly into parent config so the helper methods read the new value
        self.parent_widget.config.setdefault('appearance', {})['graph_background_opacity'] = value
        self.parent_widget._update_graph_background()
        self.parent_widget._apply_widget_background()

    def _on_opacity_released(self):
        """Full apply (saves to file) only once the user lets go of the slider."""
        self.parent_widget.apply_settings(self.get_current_settings())

    def _on_pill_opacity_preview(self, value):
        """Instantly repaint header pills while dragging — no data refresh."""
        self.pill_opacity_value.setText(f"{value}%")
        self.parent_widget.config.setdefault('appearance', {})['label_pill_opacity'] = value
        self.parent_widget._apply_header_label_styles()

    def _on_pill_opacity_released(self):
        """Full apply (saves to file) once the user releases the slider."""
        self.parent_widget.apply_settings(self.get_current_settings())

    def apply_settings(self):
        new_settings = self.get_current_settings()
        self.parent_widget.apply_settings(new_settings)
    
    def accept_settings(self):
        self.apply_settings()
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
