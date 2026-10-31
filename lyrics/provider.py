"""Общий каркас для провайдеров текстов и цепочка «кэш → LRCLIB → NetEase».

LyricsService владеет кэшем и пометкой «не найдено нигде»; отдельные
провайдеры ничего не знают про кэш и просто умеют fetch(title, artist, duration).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from lyrics.cache import LyricsCache
from lyrics.parser import normalize_query


@dataclass(frozen=True)
class LyricResult:
    synced: Optional[str]     # LRC-текст с таймкодами
    plain: Optional[str]      # обычный текст


class LyricsProvider(Protocol):
    name: str

    def fetch(self, title: str, artist: str, duration_s: Optional[float],
              show_plain: bool) -> Optional[LyricResult]:
        """None = текста точно нет; ProviderError = источник недоступен."""
        ...


class ProviderError(Exception):
    """Источник недоступен (сеть/таймаут/5xx) — не путать с «текста нет»."""


class LyricsService:
    """Кэш -> цепочка провайдеров -> пометка notfound, если никто не дал текст.

    notfound кэшируем только когда все источники ответили «текста точно нет»;
    сетевые сбои (ProviderError) при следующем опросе повторятся.
    """

    def __init__(self, cache: LyricsCache, providers: list[LyricsProvider],
                 show_plain_when_unsynced: bool = True):
        self._cache = cache
        self._providers = providers
        self._show_plain = show_plain_when_unsynced

    def get_lyrics(self, title: str, artist: str, duration_s: Optional[float]) -> Optional[LyricResult]:
        key = normalize_query(title, artist)
        cached = self._cache.get(key)
        if cached is not None:
            if cached.get("notfound"):
                return None
            return LyricResult(synced=cached.get("synced"), plain=cached.get("plain"))

        all_answered = True
        for provider in self._providers:
            try:
                result = provider.fetch(title, artist, duration_s, self._show_plain)
            except ProviderError:
                # источник недоступен — не считаем «текста нет»
                all_answered = False
                continue
            except Exception:
                # неожиданный баг провайдера не должен ронять остальные,
                # но и не позволяет asserts «точно нет»
                import traceback, sys

                traceback.print_exc(file=sys.stderr)
                all_answered = False
                continue
            if result is not None and (result.synced or (self._show_plain and result.plain)):
                self._cache.put(key, result.synced, result.plain)
                return result

        if all_answered:
            self._cache.put(key, None, None, notfound=True)  # нет нигде — неделя TTL
        return None
