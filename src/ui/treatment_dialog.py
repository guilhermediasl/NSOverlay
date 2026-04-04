from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.data.nightscout_write_thread import TreatmentWriteRequest


class TreatmentDialog(QDialog):
    """Dialog for logging insulin and carb treatments to Nightscout."""

    def __init__(self, parent: QWidget | None = None, dark_qss: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Log Treatment to Nightscout")
        self.setModal(True)
        self.setFixedSize(460, 320)

        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("Add insulin and carbs to Nightscout")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(title)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setObjectName("StatusError")
        layout.addWidget(self.status_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        self.insulin_spin = QDoubleSpinBox()
        self.insulin_spin.setRange(0.0, 100.0)
        self.insulin_spin.setDecimals(2)
        self.insulin_spin.setSingleStep(0.5)
        self.insulin_spin.setSuffix(" U")
        form.addRow("Insulin:", self.insulin_spin)

        self.carbs_spin = QSpinBox()
        self.carbs_spin.setRange(0, 500)
        self.carbs_spin.setSingleStep(1)
        self.carbs_spin.setSuffix(" g")
        form.addRow("Carbs:", self.carbs_spin)

        self.event_type_edit = QLineEdit("Meal Bolus")
        self.event_type_edit.setPlaceholderText("Meal Bolus, Correction Bolus, Carb Correction...")
        form.addRow("Event Type:", self.event_type_edit)

        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Optional note for Nightscout")
        form.addRow("Notes:", self.notes_edit)

        self.entered_by_edit = QLineEdit("NSOverlay")
        self.entered_by_edit.setPlaceholderText("NSOverlay")
        form.addRow("Entered By:", self.entered_by_edit)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        for button in (save_btn, cancel_btn):
            button.setMinimumWidth(92)
            button.setMinimumHeight(32)
            btn_row.addWidget(button)
        layout.addLayout(btn_row)

        self.setLayout(layout)
        self.setStyleSheet(dark_qss)

    def _accept(self) -> None:
        if self.insulin_spin.value() <= 0 and self.carbs_spin.value() <= 0:
            self.status_label.setText("Enter insulin or carbs before saving.")
            self.insulin_spin.setFocus()
            return
        self.accept()

    def value(self) -> TreatmentWriteRequest:
        event_type = self.event_type_edit.text().strip()
        insulin = float(self.insulin_spin.value())
        carbs = int(self.carbs_spin.value())
        if not event_type:
            if insulin > 0 and carbs > 0:
                event_type = "Meal Bolus"
            elif insulin > 0:
                event_type = "Correction Bolus"
            else:
                event_type = "Carb Correction"

        return TreatmentWriteRequest(
            nightscout_url="",
            api_secret="",
            event_type=event_type,
            insulin=insulin,
            carbs=carbs,
            notes=self.notes_edit.text().strip(),
            entered_by=self.entered_by_edit.text().strip() or "NSOverlay",
        )