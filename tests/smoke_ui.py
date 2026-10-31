"""Смоук-тест без экрана: рендер, режимы, перенос длинных строк, анимация
смены строки, защита от запаздывающей позиции. Qt offscreen.

Запуск: .venv/Scripts/python tests/smoke_ui.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import toml
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ui import LyricOverlay
from lyrics.parser import parse_lrc

DEMO_LRC = "[00:01.00] первая строка\n[00:05.00] вторая строка\n[00:09.00] третья строка"
LONG_LINE = "это очень длинная строка текста песни которая заведомо не влезет в ширину панели целиком и должна переноситься"


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    cfg = toml.load(Path(__file__).parent.parent / "config.toml")["ui"]
    cfg["mode"] = "compact"

    overlay = LyricOverlay(cfg)
    overlay.update_track("Demo Song", "Overlay Test")
    overlay.set_synced_lines(parse_lrc(DEMO_LRC))
    overlay.show()
    overlay.set_position(5.2, playing=True, duration_s=15.0)
    overlay._on_tick()

    results = {}

    def settle():
        """Форс-завершение анимации, чтобы состояние читалось синхронно."""
        overlay._anim.stop()
        overlay._anim_progress = 1.0
        overlay._on_tick()

    def check_basic():
        settle()
        results["active"] = overlay.active_text()
        results["mode"] = overlay._mode
        overlay.toggle_mode()
        overlay.set_position(9.4, playing=False, duration_s=30.0)
        settle()
        results["mode2"] = overlay._mode
        results["active2"] = overlay.active_text()

    def check_position_logic():
        # контекст: играет, позиция едет
        overlay.set_position(60.0, playing=True, duration_s=120.0)
        settle()
        est = overlay._current_position()

        # замёрзший сэмпл (тот же old value) — не должен откатывать позицию,
        # даже когда отставание больше STALE_SAMPLE_MAX_S
        time.sleep(0.4)  # как в реальности: опрос раз в ~0.5 c
        est = overlay._current_position()
        time.sleep(0.05)
        overlay.set_position(60.0, playing=True, duration_s=120.0)
        time.sleep(0.05)
        results["frozen_kept"] = overlay._current_position() >= est

        # запаздывающий сэмпл на 1 c (изменившийся) — игнорируем
        est = overlay._current_position()
        overlay.set_position(est - 1.0, playing=True, duration_s=120.0)
        results["stale_kept"] = overlay._current_position() >= est - 0.5

        # явная перемотка назад на 10 c (новое значение) — принимается
        est = overlay._current_position()
        overlay.set_position(est - 10.0, playing=True, duration_s=120.0)
        results["seek_applied"] = overlay._current_position() <= est - 5.0

        # длинная строка: рендер не падает, plain-ветка живёт
        overlay.set_plain_text("\n".join([LONG_LINE] * 6))
        settle()
        results["plain_ok"] = overlay.active_text() != ""

        # РЕГРЕССИЯ plain-режима: отрисованный индекс должен меняться при
        # движении позиции (раньше _on_tick выходил рано и строка застывала)
        o2 = LyricOverlay(cfg)
        o2.set_plain_text("\n".join(f"строка {i}" for i in range(10)))
        o2.set_position(0.0, playing=True, duration_s=240.0)
        o2._on_tick()
        results["plain_idx_start"] = o2._active_idx
        o2.set_position(130.0, playing=True, duration_s=240.0)
        o2._anim.stop()
        o2._anim_progress = 1.0
        o2._on_tick()
        results["plain_idx_moved"] = o2._active_idx

        # drag: колбэк сохранения позиции вызывается с координатами окна
        saved = {}
        overlay.on_drag_finished = lambda x, y: saved.update(x=x, y=y)
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QEvent, Qt as Qtt
        press = QMouseEvent(QEvent.MouseButtonPress, QPointF(10, 10), QPointF(110, 110),
                            Qtt.LeftButton, Qtt.LeftButton, Qtt.NoModifier)
        overlay.mousePressEvent(press)
        overlay.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(60, 60), QPointF(160, 160),
                                           Qtt.LeftButton, Qtt.LeftButton, Qtt.NoModifier))
        overlay.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(60, 60), QPointF(160, 160),
                                              Qtt.LeftButton, Qtt.NoButton, Qtt.NoModifier))
        results["drag_saved"] = "x" in saved and saved["x"] == overlay.x()

    def finish():
        app.quit()

    QTimer.singleShot(200, check_basic)
    QTimer.singleShot(400, check_position_logic)
    QTimer.singleShot(800, finish)
    app.exec()

    assert results["active"] == "вторая строка", results
    assert results["mode"] == "compact" and results["mode2"] == "karaoke"
    assert results["active2"] == "третья строка", results
    assert results["frozen_kept"], results
    assert results["stale_kept"], results
    assert results["seek_applied"], results
    assert results["plain_ok"], results
    assert results["plain_idx_start"] == 0, results
    assert results["plain_idx_moved"] == 5, results
    assert results["drag_saved"], results
    print("SMOKE_UI_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
