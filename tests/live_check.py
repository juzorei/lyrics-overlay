"""Живой интеграционный тест: читает трек из SMTC, гоняет приложение
без UI-видимости и печатает, что UI реально получил от LRCLIB.

Запуск (нужен играющий плеер): .venv/Scripts/python tests/live_check.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import toml
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from main import App, UiBridge
from ui import LyricOverlay


def main() -> int:
    cfg = toml.load(Path(__file__).parent.parent / "config.toml")
    app = QApplication([])

    overlay = LyricOverlay(cfg["ui"])
    overlay.show()
    bridge = UiBridge()

    def on_not_found():
        print("LRCLIB_RESULT: not_found", flush=True)

    def on_synced(lines):
        print(f"LRCLIB_RESULT: synced, {len(lines)} lines, first: {lines[0].text!r}", flush=True)

    def on_plain(text):
        print(f"LRCLIB_RESULT: plain, {len(text)} chars, first: {text.splitlines()[0]!r}", flush=True)

    bridge.not_found.connect(on_not_found)
    bridge.synced_ready.connect(on_synced)
    bridge.plain_ready.connect(on_plain)

    application = App(cfg, bridge, demo=False)

    def report():
        print("ACTIVE_LABEL:", repr(overlay._active_label.text()), flush=True)
        print("NEXT_LABEL:", repr(overlay._next_label.text()), flush=True)
        print("TITLE_LABEL:", repr(overlay._title_label.text()), flush=True)
        app.quit()

    poll = QTimer()
    poll.timeout.connect(lambda: application.poll_once(overlay))
    poll.start(400)

    report_timer = QTimer.singleShot(14000, report)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
