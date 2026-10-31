"""Демо-монитор: крутит фиктивный трек с растущей позицией.

Используется для разработки/отладки UI без запущенного плеера,
через флаг --demo.
"""

from __future__ import annotations

import time

from players.base import Track

_DEMO_DURATION_US = 300_000_000  # 300 s в микросекундах


class DemoMonitor:
    """Отдаёт бесконечно зацикленный демо-трек."""

    def __init__(self, duration_s: float = 300.0):
        self._start = time.monotonic()
        self._duration_s = duration_s

    def current_track(self) -> Track:
        elapsed = time.monotonic() - self._start
        pos_us = int((elapsed % self._duration_s) * 1_000_000)
        return Track(
            title="Demo Song",
            artist="Overlay Test",
            album="Lyrics Overlay Demo",
            duration_us=int(self._duration_s * 1_000_000),
            position_us=pos_us,
            playing=True,
            source="demo",
        )

    def close(self) -> None:
        pass
