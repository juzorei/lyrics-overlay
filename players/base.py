"""Track dataclass and the player-monitor interface shared by all platforms."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class Track:
    title: str
    artist: str = ""
    album: str = ""
    duration_us: int = 0            # микросекунды, 0 если неизвестно
    position_us: int = 0             # микросекунды, 0 если неизвестно
    playing: bool = False
    source: str = ""                 # имя плеера/приложения
    artwork: bytes = b""             # байты обложки (PNG/JPEG), пусто если нет

    @property
    def key(self) -> str:
        """Стабильный ключ для кэша: нормализованные title+artist."""
        from lyrics.parser import normalize_query

        return normalize_query(self.title, self.artist)

    @property
    def duration_s(self) -> Optional[float]:
        return self.duration_us / 1_000_000 if self.duration_us else None

    @property
    def position_s(self) -> float:
        return self.position_us / 1_000_000

    @property
    def is_empty(self) -> bool:
        return not self.title.strip()


class PlayerMonitor(Protocol):
    """Общий интерфейс: выдаёт текущий Track или None, если ничего не играет."""

    def current_track(self) -> Optional[Track]: ...

    def close(self) -> None: ...
