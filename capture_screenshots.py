"""
Captures documentation screenshots for NSOverlay.
Run once from the project root with the venv activated:

    python capture_screenshots.py

Output: docs/images/widget_main.png
        docs/images/settings_dialog.png
        docs/images/setup_wizard.png
"""
import sys
import os

# Make sure we resolve paths the same way nsoverlay.py does
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor

# Import classes from the main module (the __main__ block is skipped on import)
from nsoverlay import (
    GlucoseWidget, SetupWizard, SettingsDialog, CONFIG_FILE, _BASE_DIR
)

SAVE_DIR = os.path.join(_BASE_DIR, "docs", "images")
os.makedirs(SAVE_DIR, exist_ok=True)

# ─── helpers ──────────────────────────────────────────────────────────────────

def save_widget_screen_grab(app: QApplication, target: QWidget, filename: str):
    """Capture the widget as it appears on screen (respects transparency/compositing)."""
    target.raise_()
    target.activateWindow()
    screen = app.primaryScreen()
    geo = target.frameGeometry()
    pixmap = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
    dest = os.path.join(SAVE_DIR, filename)
    pixmap.save(dest, "PNG")
    print(f"  ✓ {dest}")


def save_grab(widget: QWidget, filename: str):
    """Capture a dialog/window using QWidget.grab() — no transparency issues."""
    pixmap = widget.grab()
    dest = os.path.join(SAVE_DIR, filename)
    pixmap.save(dest, "PNG")
    print(f"  ✓ {dest}")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("NSOverlay-Screenshot")

    seq_index = [0]   # mutable counter so nested closures can advance it

    # ── Step 1: main widget ──────────────────────────────────────────────────
    print("\n[1/3] Launching widget — waiting 6 s for data to load...")
    widget = GlucoseWidget()

    # Put it in a predictable place near the top-left of the screen
    screen_geo = app.primaryScreen().availableGeometry()
    w_x = screen_geo.x() + 60
    w_y = screen_geo.y() + 60
    widget.move(w_x, w_y)
    widget.show()

    # ── Step 2: settings dialog (shown 1 s after widget screenshot) ──────────
    def capture_settings():
        print("\n[2/3] Opening Settings dialog...")
        dlg = SettingsDialog(widget, widget.config)
        dlg.setModal(False)
        dlg.show()
        dlg.raise_()

        def do_settings_grab():
            save_grab(dlg, "settings_dialog.png")
            dlg.close()
            QTimer.singleShot(500, capture_wizard)

        QTimer.singleShot(400, do_settings_grab)

    # ── Step 3: setup wizard ─────────────────────────────────────────────────
    def capture_wizard():
        print("\n[3/3] Opening Setup Wizard...")
        wiz = SetupWizard()
        # Pre-fill with placeholder values so the dialog looks informative
        wiz.url_input.setText("https://your-nightscout.fly.dev")
        wiz.secret_input.setEchoMode(wiz.secret_input.EchoMode.Normal)
        wiz.secret_input.setText("••••••••••••")
        wiz.show()
        wiz.raise_()

        def do_wizard_grab():
            save_grab(wiz, "setup_wizard.png")
            wiz.close()
            widget.close()
            app.quit()

        QTimer.singleShot(400, do_wizard_grab)

    # ── Fire the sequence ────────────────────────────────────────────────────
    def capture_widget():
        print("\n  Taking widget screenshot (grab)...")
        save_grab(widget, "widget_main.png")
        QTimer.singleShot(200, capture_settings)

    QTimer.singleShot(6000, capture_widget)

    print("  (widget is on screen — don't move it for the next 6 seconds)\n")
    sys.exit(app.exec())


if __name__ == "__main__":
    if not os.path.exists(CONFIG_FILE):
        print("ERROR: config.json not found. Run the main app first to create it.")
        sys.exit(1)
    main()
