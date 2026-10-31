"""Платформенные мониторы. create_monitor() выбирает реализацию по ОС."""

from __future__ import annotations

import platform
from typing import Optional

from players.base import PlayerMonitor, Track


def create_monitor(demo: bool = False) -> PlayerMonitor:
    """Возвращает монитор текущей платформы или демо-монитор."""
    if demo:
        from players.demo import DemoMonitor

        return DemoMonitor()
    system = platform.system()
    if system == "Windows":
        from players.windows_smtc import SmtcMonitor

        return SmtcMonitor()
    if system == "Linux":
        from players.linux_mpris import MprisMonitor

        return MprisMonitor()
    raise RuntimeError(f"Unsupported platform: {system}")
