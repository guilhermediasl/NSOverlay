from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core import build_initial_config


class SetupWizard(QDialog):
    """First-run wizard: asks for Nightscout URL and API secret, writes config.json."""

    def __init__(
        self, config_file: str, dark_qss: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._config_file = config_file
        self.setWindowTitle("NSOverlay — First Run Setup")
        self.setFixedSize(480, 300)
        self.setWindowFlags(Qt.WindowType.Dialog)

        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("Welcome to NSOverlay")
        title.setFont(QFont("sans-serif", 14, QFont.Weight.Bold))
        title.setObjectName("DialogTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Enter your Nightscout credentials to get started.")
        subtitle.setObjectName("DialogSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self.status_label.setObjectName("StatusError")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self.setStyleSheet(dark_qss)

    def _save(self) -> None:
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

        config = build_initial_config(url, secret)
        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
            self.accept()
        except Exception as e:
            self.status_label.setText(f"Could not write config: {e}")
            self.save_btn.setEnabled(True)
