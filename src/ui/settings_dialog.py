from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, cast

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


JsonDict = dict[str, Any]


class SettingsHost(Protocol):
    nightscout_url: str
    api_secret_raw: str
    config: JsonDict

    def apply_settings(self, new_config: JsonDict, fetch_remote: bool = False) -> None: ...
    def _update_graph_background(self) -> None: ...
    def _apply_widget_background(self) -> None: ...
    def _apply_header_background(self) -> None: ...
    def _apply_header_label_styles(self, glucose_text_color: str | None = None) -> None: ...


@dataclass
class PillConfig:
    event_type: str = ""
    label: str = ""
    show_field: str = "notes"
    suffix: str = ""
    color: str = "#80e8e0"
    bold: bool = False
    sum_daily: bool = False
    enabled: bool = True
    max_age_hours: float | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PillConfig":
        max_age_raw = data.get("max_age_hours")
        max_age = None
        if isinstance(max_age_raw, (int, float)) and max_age_raw > 0:
            max_age = float(max_age_raw)
        return cls(
            event_type=str(data.get("event_type", "")),
            label=str(data.get("label", "")),
            show_field=str(data.get("show_field", "notes")),
            suffix=str(data.get("suffix", "")),
            color=str(data.get("color", "#80e8e0")),
            bold=bool(data.get("bold", False)),
            sum_daily=bool(data.get("sum_daily", False)),
            enabled=bool(data.get("enabled", True)),
            max_age_hours=max_age,
        )

    def to_dict(self) -> JsonDict:
        data: JsonDict = {
            "event_type": self.event_type,
            "label": self.label,
            "show_field": self.show_field,
            "suffix": self.suffix,
            "color": self.color,
            "bold": self.bold,
            "sum_daily": self.sum_daily,
            "enabled": self.enabled,
        }
        if self.max_age_hours is not None and self.max_age_hours > 0:
            data["max_age_hours"] = self.max_age_hours
        return data


class ColorButton(QPushButton):
    """Push-button that shows a color swatch and opens QColorDialog on click."""

    colorChanged = pyqtSignal(str)

    def __init__(self, color: str = "#ffffff", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color).name() if QColor(color).isValid() else "#ffffff"
        self.setFixedSize(128, 30)
        self._refresh()
        self.clicked.connect(self._pick)

    def _pick(self) -> None:
        original_color = self._color
        dlg = QColorDialog(QColor(self._color), self)
        dlg.setWindowTitle("Choose Pill Text Color")
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dlg.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)

        def _on_live_color(c: QColor) -> None:
            if c.isValid():
                self._set_color(c.name(), emit_signal=True)

        dlg.currentColorChanged.connect(_on_live_color)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            chosen = dlg.currentColor()
            if chosen.isValid():
                self._set_color(chosen.name(), emit_signal=True)
        else:
            self._set_color(original_color, emit_signal=True)

    def _set_color(self, color: str, emit_signal: bool = False) -> None:
        normalized = QColor(color).name() if QColor(color).isValid() else self._color
        if normalized == self._color:
            return
        self._color = normalized
        self._refresh()
        if emit_signal:
            self.colorChanged.emit(self._color)

    def _refresh(self) -> None:
        r = int(self._color[1:3], 16)
        g = int(self._color[3:5], 16)
        b = int(self._color[5:7], 16)
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        text = "#000000" if luma > 128 else "#ffffff"
        self.setStyleSheet(
            f"background: {self._color}; color: {text}; "
            "border: 1px solid #4a5d80; border-radius: 10px; "
            "font-size: 11px; font-weight: 600; padding: 0px 10px;"
        )
        self.setText(f"{self._color.upper()}  Pick")

    @property
    def color(self) -> str:
        return self._color

    @color.setter
    def color(self, val: str) -> None:
        self._set_color(val, emit_signal=False)


class PillEditDialog(QDialog):
    """Sub-dialog for adding or editing a single header pill entry."""

    _FIELD_OPTIONS: list[str] = [
        "notes",
        "amount",
        "insulin",
        "carbs",
        "duration",
        "absolute",
        "rate",
        "enteredBy",
        "eventType",
    ]

    def __init__(
        self,
        parent: QWidget | None,
        dark_qss: str,
        pill: Mapping[str, Any] | PillConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self._dark_qss = dark_qss
        self.setWindowTitle("Edit Pill" if pill else "Add Pill")
        self.setModal(True)
        self.setFixedSize(480, 480)
        pill_data: JsonDict
        if isinstance(pill, PillConfig):
            pill_data = pill.to_dict()
        elif pill is None:
            pill_data = {}
        else:
            pill_data = dict(pill)

        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(40)
        layout.addWidget(self.preview_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        self.event_type_edit = QLineEdit(pill_data.get("event_type", ""))
        self.event_type_edit.setPlaceholderText("e.g. Bolus|Meal Bolus")
        form.addRow("Event Type:", self.event_type_edit)

        self.label_edit = QLineEdit(pill_data.get("label", ""))
        self.label_edit.setPlaceholderText("e.g. Bolus")
        form.addRow("Label:", self.label_edit)

        self.field_combo = QComboBox()
        self.field_combo.addItems(self._FIELD_OPTIONS)
        cur = pill_data.get("show_field", "notes")
        idx = self.field_combo.findText(cur)
        self.field_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Show Field:", self.field_combo)

        self.suffix_edit = QLineEdit(pill_data.get("suffix", ""))
        self.suffix_edit.setPlaceholderText("e.g. U")
        form.addRow("Suffix:", self.suffix_edit)

        self.color_btn = ColorButton(str(pill_data.get("color", "#80e8e0")))
        color_row = QWidget()
        color_layout = QHBoxLayout()
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(8)
        self.color_hex_edit = QLineEdit(self.color_btn.color.upper())
        self.color_hex_edit.setMaxLength(7)
        self.color_hex_edit.setFixedWidth(96)
        self.color_hex_edit.setPlaceholderText("#RRGGBB")
        self.color_hex_edit.editingFinished.connect(self._apply_hex_color)
        color_layout.addWidget(self.color_btn)
        color_layout.addWidget(self.color_hex_edit)
        color_layout.addStretch()
        color_row.setLayout(color_layout)
        form.addRow("Color:", color_row)

        self.bold_chk = QCheckBox("Bold text")
        self.bold_chk.setChecked(bool(pill_data.get("bold", False)))
        form.addRow("Style:", self.bold_chk)

        self.sum_daily_chk = QCheckBox("Sum all matching entries for today")
        self.sum_daily_chk.setChecked(bool(pill_data.get("sum_daily", False)))
        form.addRow("", self.sum_daily_chk)

        self.enabled_chk = QCheckBox("Show this pill in header")
        self.enabled_chk.setChecked(bool(pill_data.get("enabled", True)))
        form.addRow("", self.enabled_chk)

        self.max_age_spin = QDoubleSpinBox()
        self.max_age_spin.setRange(0, 72)
        self.max_age_spin.setDecimals(1)
        self.max_age_spin.setSuffix(" h")
        self.max_age_spin.setSpecialValueText("—")
        self.max_age_spin.setValue(float(pill_data.get("max_age_hours", 0)))
        form.addRow("Max Age:", self.max_age_spin)

        layout.addLayout(form)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.setMinimumWidth(90)
        ok_btn.setMinimumHeight(32)
        ok_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(90)
        cancel_btn.setMinimumHeight(32)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)
        self.setStyleSheet(self._dark_qss)

        self.event_type_edit.textChanged.connect(self._update_preview)
        self.label_edit.textChanged.connect(self._update_preview)
        self.field_combo.currentTextChanged.connect(self._update_preview)
        self.suffix_edit.textChanged.connect(self._update_preview)
        self.color_btn.colorChanged.connect(self._update_preview)
        self.color_btn.colorChanged.connect(lambda c: self.color_hex_edit.setText(c.upper()))
        self.bold_chk.toggled.connect(self._update_preview)
        self._update_preview()

    def _apply_hex_color(self) -> None:
        raw = self.color_hex_edit.text().strip()
        if raw and not raw.startswith("#"):
            raw = f"#{raw}"
        c = QColor(raw)
        if c.isValid():
            self.color_btn.color = c.name()
            self.color_hex_edit.setText(c.name().upper())
            self._update_preview()
        else:
            self.color_hex_edit.setText(self.color_btn.color.upper())

    def _preview_value_for_field(self, field_name: str) -> str:
        sample_values = {
            "notes": "sample",
            "amount": "12",
            "insulin": "2.5",
            "carbs": "18",
            "duration": "30",
            "absolute": "0.85",
            "rate": "1.20",
            "enteredBy": "you",
            "eventType": "Event",
        }
        return sample_values.get(field_name, "value")

    def _update_preview(self) -> None:
        event_type = self.event_type_edit.text().strip()
        label_text = self.label_edit.text().strip() or event_type or "Pill"
        field_name = self.field_combo.currentText()
        value_text = self._preview_value_for_field(field_name)
        suffix = self.suffix_edit.text()
        preview_text = f"{label_text}: {value_text}{suffix}" if value_text else label_text

        font_weight = "700" if self.bold_chk.isChecked() else "500"
        self.preview_label.setText(preview_text)
        self.preview_label.setStyleSheet(
            f"color: {self.color_btn.color}; background-color: rgba(255, 255, 255, 14); "
            "border: 1px solid #3a4760; border-radius: 8px; padding: 3px 8px; "
            f"font-size: 12px; font-weight: {font_weight};"
        )

    def _accept(self) -> None:
        if not self.event_type_edit.text().strip():
            self.event_type_edit.setFocus()
            return
        self.accept()

    def value(self) -> JsonDict:
        d: JsonDict = {
            "event_type": self.event_type_edit.text().strip(),
            "label": self.label_edit.text().strip(),
            "show_field": self.field_combo.currentText(),
            "suffix": self.suffix_edit.text(),
            "color": self.color_btn.color,
            "bold": self.bold_chk.isChecked(),
            "sum_daily": self.sum_daily_chk.isChecked(),
            "enabled": self.enabled_chk.isChecked(),
        }
        if self.max_age_spin.value() > 0:
            d["max_age_hours"] = self.max_age_spin.value()
        return d

    def value_dataclass(self) -> PillConfig:
        return PillConfig.from_mapping(self.value())


class SettingsDialog(QDialog):
    """Tabbed settings dialog covering all config.json parameters."""

    def __init__(self, parent: SettingsHost, config: JsonDict, dark_qss: str) -> None:
        super().__init__(cast(QWidget, parent))
        self.parent_widget = parent
        import copy

        self._dark_qss = dark_qss
        self.config = copy.deepcopy(config)
        self._pills: list[PillConfig] = [
            PillConfig.from_mapping(item)
            for item in self.config.get("header_pills", [])
            if isinstance(item, dict)
        ]

        self.setWindowTitle("Settings — NSOverlay")
        self.setModal(True)
        self.setMinimumSize(560, 640)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_connection_tab(), "Connection")
        self.tabs.addTab(self._build_graph_tab(), "Graph")
        self.tabs.addTab(self._build_appearance_tab(), "Appearance")
        self.tabs.addTab(self._build_colors_tab(), "Colors")
        self.tabs.addTab(self._build_pills_tab(), "Pills")
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
        self.setStyleSheet(self._dark_qss)

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
        for display, data in (
            ("Solid", "solid"),
            ("Dash", "dash"),
            ("Dot", "dot"),
            ("Dash-Dot", "dashdot"),
        ):
            self.line_style_combo.addItem(display, data)
        cur = ap.get("graph_line_style", "solid")
        self.line_style_combo.setCurrentIndex(max(0, self.line_style_combo.findData(cur)))
        form.addRow("Line Style:", self.line_style_combo)

        self.line_width_slider, line_row = self._slider_row(1, 8, ap.get("graph_line_width", 2))
        form.addRow("Line Width:", line_row)

        self.smooth_line_chk = QCheckBox()
        self.smooth_line_chk.setChecked(bool(ap.get("graph_line_smooth", False)))
        self.smooth_line_chk.setToolTip("Use smooth monotone cubic curves instead of straight line segments")
        form.addRow("Smooth Curve:", self.smooth_line_chk)

        self.dot_size_slider, dot_row = self._slider_row(2, 20, c.get("data_point_size", 6))
        form.addRow("Dot Size:", dot_row)

        self.adaptive_dot_chk = QCheckBox()
        self.adaptive_dot_chk.setChecked(bool(c.get("adaptive_dot_size", False)))
        form.addRow("Adaptive Dot Size:", self.adaptive_dot_chk)

        self.show_delta_chk = QCheckBox()
        self.show_delta_chk.setChecked(bool(c.get("show_delta", True)))
        form.addRow("Show Delta:", self.show_delta_chk)

        self.show_float_glucose_chk = QCheckBox()
        self.show_float_glucose_chk.setChecked(bool(c.get("show_float_glucose", True)))
        self.show_float_glucose_chk.setToolTip("Show or hide the floating glucose value label inside the graph")
        form.addRow("Show Floating Value:", self.show_float_glucose_chk)

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

        self.graph_opacity_slider, graph_op_row = self._pct_slider_row(ap.get("graph_background_opacity", 100))
        self.graph_opacity_slider.valueChanged.connect(self._preview_graph_opacity)
        self.graph_opacity_slider.sliderReleased.connect(lambda: self.parent_widget.apply_settings(self._collect()))
        form.addRow("Graph Opacity:", graph_op_row)

        self.transparency_enabled_chk = QCheckBox()
        self.transparency_enabled_chk.setChecked(bool(ap.get("transparency_enabled", True)))
        self.transparency_enabled_chk.setToolTip(
            "Enable to use the opacity sliders; disable to force 100% opacity for graph and header"
        )
        self.transparency_enabled_chk.toggled.connect(self._toggle_transparency_enabled_from_checkbox)
        form.addRow("Enable Transparency:", self.transparency_enabled_chk)

        self.pill_opacity_slider, pill_op_row = self._pct_slider_row(ap.get("label_pill_opacity", 67))
        self.pill_opacity_slider.valueChanged.connect(self._preview_pill_opacity)
        self.pill_opacity_slider.sliderReleased.connect(lambda: self.parent_widget.apply_settings(self._collect()))
        form.addRow("Pill Opacity:", pill_op_row)

        self.zone_opacity_spin = QSpinBox()
        self.zone_opacity_spin.setRange(0, 255)
        self.zone_opacity_spin.setToolTip("Alpha value 0-255 used for target zone fill color")
        self.zone_opacity_spin.setValue(ap.get("target_zone_opacity", 20))
        form.addRow("Zone Fill Alpha:", self.zone_opacity_spin)

        self.grid_opacity_slider, grid_op_row = self._pct_slider_row(int(ap.get("grid_opacity", 0.3) * 100))
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
        ui_c = colors.get("ui", {})
        graph_c = colors.get("graph", {})
        gr_c = colors.get("glucose_ranges", {})
        tz_c = colors.get("target_zones", {})

        inner = QWidget()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)
        form.setContentsMargins(16, 12, 16, 12)

        def _sep(title):
            lbl = QLabel(f"  {title}")
            lbl.setObjectName("SectionCaption")
            form.addRow(lbl)

        _sep("Glucose Ranges")
        self.c_low = ColorButton(gr_c.get("low", "#ff4444"))
        self.c_inrange = ColorButton(gr_c.get("in_range", "#00d4aa"))
        self.c_high = ColorButton(gr_c.get("high", "#ff8800"))
        form.addRow("Low:", self.c_low)
        form.addRow("In Range:", self.c_inrange)
        form.addRow("High:", self.c_high)

        _sep("Target Zone Lines")
        self.c_low_line = ColorButton(tz_c.get("low_line", "#ff4444"))
        self.c_high_line = ColorButton(tz_c.get("high_line", "#ff8800"))
        form.addRow("Low Line:", self.c_low_line)
        form.addRow("High Line:", self.c_high_line)

        _sep("Graph")
        self.c_graph_bg = ColorButton(graph_c.get("background", "#1a1a1a"))
        self.c_main_line = ColorButton(graph_c.get("main_line", "#a0a0a0"))
        self.c_axis = ColorButton(graph_c.get("axis_lines", "#888888"))
        self.c_axis_text = ColorButton(graph_c.get("axis_text", "#cccccc"))
        self.c_time_line = ColorButton(graph_c.get("current_time_line", "#888888"))
        form.addRow("Background:", self.c_graph_bg)
        form.addRow("Main Line:", self.c_main_line)
        form.addRow("Axis Lines:", self.c_axis)
        form.addRow("Axis Text:", self.c_axis_text)
        form.addRow("Current Time Line:", self.c_time_line)

        _sep("UI Labels")
        self.c_glucose_text = ColorButton(ui_c.get("main_glucose_text", "#ffffff"))
        self.c_time_label = ColorButton(ui_c.get("time_label", "#cccccc"))
        self.c_age_label = ColorButton(ui_c.get("age_label", "#999999"))
        self.c_widget_bg = ColorButton(ui_c.get("widget_background", "#2a2a2a"))
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
            "Double-click an entry to edit it."
        )
        info.setObjectName("DialogSubtitle")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.pills_list = QListWidget()
        self.pills_list.setAlternatingRowColors(True)
        self.pills_list.itemDoubleClicked.connect(self._edit_pill)
        self._refresh_pills_list()
        layout.addWidget(self.pills_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add")
        edit_btn = QPushButton("Edit")
        remove_btn = QPushButton("Remove")
        toggle_btn = QPushButton("Show/Hide")
        up_btn = QPushButton("↑")
        dn_btn = QPushButton("↓")
        up_btn.setFixedWidth(32)
        dn_btn.setFixedWidth(32)
        add_btn.clicked.connect(self._add_pill)
        edit_btn.clicked.connect(self._edit_pill)
        remove_btn.clicked.connect(self._remove_pill)
        toggle_btn.clicked.connect(self._toggle_pill_visibility)
        up_btn.clicked.connect(self._move_pill_up)
        dn_btn.clicked.connect(self._move_pill_down)
        for b in (add_btn, edit_btn, remove_btn, toggle_btn, up_btn, dn_btn):
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        w.setLayout(layout)
        return w

    def _refresh_pills_list(self) -> None:
        self.pills_list.clear()
        for p in self._pills:
            label = p.label or p.event_type or "?"
            field = p.show_field
            suffix = p.suffix
            max_age = p.max_age_hours if p.max_age_hours is not None else "—"
            mode = "daily sum" if p.sum_daily else f"max {max_age}h"
            visibility = "ON" if p.enabled else "OFF"
            weight = "bold" if p.bold else "normal"
            self.pills_list.addItem(f"[{visibility}] {label}  ·  {field} {suffix}  [{mode}] [{weight}]")

    def _add_pill(self) -> None:
        dlg = PillEditDialog(self, self._dark_qss)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._pills.append(dlg.value_dataclass())
            self._refresh_pills_list()
            self.pills_list.setCurrentRow(len(self._pills) - 1)

    def _edit_pill(self, *_: Any) -> None:
        row = self.pills_list.currentRow()
        if row < 0:
            return
        dlg = PillEditDialog(self, self._dark_qss, self._pills[row])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._pills[row] = dlg.value_dataclass()
            self._refresh_pills_list()
            self.pills_list.setCurrentRow(row)

    def _remove_pill(self) -> None:
        row = self.pills_list.currentRow()
        if row < 0:
            return
        self._pills.pop(row)
        self._refresh_pills_list()

    def _toggle_pill_visibility(self) -> None:
        row = self.pills_list.currentRow()
        if row < 0:
            return
        pill = self._pills[row]
        self._pills[row] = PillConfig(
            event_type=pill.event_type,
            label=pill.label,
            show_field=pill.show_field,
            suffix=pill.suffix,
            color=pill.color,
            bold=pill.bold,
            sum_daily=pill.sum_daily,
            enabled=not pill.enabled,
            max_age_hours=pill.max_age_hours,
        )
        self._refresh_pills_list()
        self.pills_list.setCurrentRow(row)

    def _move_pill_up(self) -> None:
        row = self.pills_list.currentRow()
        if row > 0:
            self._pills[row - 1], self._pills[row] = self._pills[row], self._pills[row - 1]
            self._refresh_pills_list()
            self.pills_list.setCurrentRow(row - 1)

    def _move_pill_down(self) -> None:
        row = self.pills_list.currentRow()
        if 0 <= row < len(self._pills) - 1:
            self._pills[row + 1], self._pills[row] = self._pills[row], self._pills[row + 1]
            self._refresh_pills_list()
            self.pills_list.setCurrentRow(row + 1)

    @staticmethod
    def _form_widget() -> tuple[QWidget, QFormLayout]:
        w = QWidget()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)
        w.setLayout(form)
        return w, form

    @staticmethod
    def _slider_row(lo: int, hi: int, val: int) -> tuple[QSlider, QWidget]:
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
    def _pct_slider_row(val: int) -> tuple[QSlider, QWidget]:
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

    def _preview_graph_opacity(self, v: int) -> None:
        self.parent_widget.config.setdefault("appearance", {})["graph_background_opacity"] = v
        self.parent_widget._update_graph_background()
        self.parent_widget._apply_widget_background()
        self.parent_widget._apply_header_background()

    def _toggle_transparency_enabled_from_checkbox(self, checked: bool) -> None:
        self.config.setdefault("appearance", {})["transparency_enabled"] = checked
        self.parent_widget.config.setdefault("appearance", {})["transparency_enabled"] = checked
        self.parent_widget._update_graph_background()
        self.parent_widget._apply_widget_background()
        self.parent_widget._apply_header_background()
        self.parent_widget._apply_header_label_styles()
        self.parent_widget.apply_settings(self._collect())

    def _preview_pill_opacity(self, v: int) -> None:
        self.parent_widget.config.setdefault("appearance", {})["label_pill_opacity"] = v
        self.parent_widget._apply_header_label_styles()

    def _collect(self) -> JsonDict:
        c = self.config
        ap = dict(c.get("appearance", {}))

        ap["graph_line_width"] = self.line_width_slider.value()
        ap["graph_line_style"] = self.line_style_combo.currentData()
        ap["graph_line_smooth"] = self.smooth_line_chk.isChecked()
        ap["graph_background_opacity"] = self.graph_opacity_slider.value()
        ap["transparency_enabled"] = self.transparency_enabled_chk.isChecked()
        ap["label_pill_opacity"] = self.pill_opacity_slider.value()
        ap["target_zone_opacity"] = self.zone_opacity_spin.value()
        ap["grid_opacity"] = round(self.grid_opacity_slider.value() / 100, 2)
        ap["marker_outline_width"] = self.outline_width_spin.value()
        ap["show_y_label"] = self.show_y_label_chk.isChecked()

        orig_colors = c.get("appearance", {}).get("colors", {})
        orig_ui = orig_colors.get("ui", {})
        orig_tz = orig_colors.get("target_zones", {})
        ap["colors"] = {
            "glucose_ranges": {
                "low": self.c_low.color,
                "in_range": self.c_inrange.color,
                "high": self.c_high.color,
            },
            "target_zones": {
                "low_line": self.c_low_line.color,
                "high_line": self.c_high_line.color,
                "target_fill": orig_tz.get("target_fill", "#00d4aa"),
            },
            "graph": {
                "background": self.c_graph_bg.color,
                "main_line": self.c_main_line.color,
                "axis_lines": self.c_axis.color,
                "axis_text": self.c_axis_text.color,
                "axis_labels": self.c_axis_text.color,
                "current_time_line": self.c_time_line.color,
            },
            "ui": {
                "main_glucose_text": self.c_glucose_text.color,
                "time_label": self.c_time_label.color,
                "age_label": self.c_age_label.color,
                "widget_background": self.c_widget_bg.color,
                "close_button": orig_ui.get("close_button", "#ff4444"),
                "close_button_hover": orig_ui.get("close_button_hover", "#ff6666"),
                "close_button_background": orig_ui.get("close_button_background", "rgba(0,0,0,150)"),
                "close_button_hover_background": orig_ui.get("close_button_hover_background", "rgba(255,68,68,200)"),
            },
        }

        new_config = dict(c)
        new_config["nightscout_url"] = self.url_edit.text().strip().rstrip("/")
        new_config["api_secret_raw"] = self.secret_edit.text().strip()
        new_config["refresh_interval"] = self.refresh_spin.value()
        new_config["timezone_offset"] = self.tz_spin.value()
        new_config["time_window_hours"] = self.time_window_spin.value()
        new_config["entries_to_fetch"] = self.entries_spin.value()
        new_config["target_low"] = self.target_low_spin.value()
        new_config["target_high"] = self.target_high_spin.value()
        new_config["data_point_size"] = self.dot_size_slider.value()
        new_config["adaptive_dot_size"] = self.adaptive_dot_chk.isChecked()
        new_config["show_delta"] = self.show_delta_chk.isChecked()
        new_config["show_float_glucose"] = self.show_float_glucose_chk.isChecked()
        new_config["gradient_interpolation"] = self.gradient_chk.isChecked()
        new_config["glucose_font_size"] = self.glucose_font_spin.value()
        new_config["time_font_size"] = self.time_font_spin.value()
        new_config["age_font_size"] = self.age_font_spin.value()
        new_config["header_pills"] = [pill.to_dict() for pill in self._pills]
        new_config["appearance"] = ap
        return new_config

    def _apply(self) -> None:
        self.parent_widget.apply_settings(self._collect())

    def _ok(self) -> None:
        self._apply()
        self.accept()
