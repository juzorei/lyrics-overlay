"""Провайдер NetEase Cloud Music (music.163.com) — API без ключей.

Публичные эндпоинты 163.com, которые дёшево и без авторизации отдают
и LRC (synced), и tlyric (перевод). Используются как fallback после LRCLIB.
Живые проверки: поиск и тексты работают (2026-10).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from lyrics.provider import LyricResult, ProviderError

BASE = "https://music.163.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (lyrics-overlay/0.1)",
    "Referer": "https://music.163.com/",
}

# допуски при сверке кандидата с играющим треком
DURATION_TOLERANCE_S = 5.0


class NeteaseProvider:
    name = "netease"

    def __init__(self, timeout: float = 10.0, limit: int = 5):
        self._timeout = timeout
        self._limit = limit

    def fetch(self, title: str, artist: str, duration_s: Optional[float],
              show_plain: bool) -> Optional[LyricResult]:
        """Берём кандидата, совпадающего по длительности (±5 c) И исполнителю.

        Search 163 часто подсовывает каверы/ремиксы/однофамильцев — текст
        чужой песни хуже, чем никакого.
        """
        songs = self._search(title, artist)
        if not songs:
            return None

        def duration_ok(song) -> bool:
            if not duration_s:
                return True
            d = (song.get("duration") or 0) / 1000
            return d > 0 and abs(d - duration_s) <= DURATION_TOLERANCE_S

        def artist_ok(song) -> bool:
            if not artist:
                return True
            from lyrics.parser import normalize_query

            arts = song.get("artists") or []
            names = " ".join(str(a.get("name", "")) for a in arts)
            if not names:
                return False  # нет данных об исполнителе — не рискуем
            a_key = normalize_query(artist, "")
            return a_key in normalize_query(names, "")

        matched = [s for s in songs if duration_ok(s) and artist_ok(s)]
        if not matched:
            matched = [s for s in songs if artist_ok(s)]  # длительность у 163 бывает врёт
        if not matched:
            return None
        for song in matched:
            lrc = self._lyrics(song["id"])
            if lrc:
                return LyricResult(synced=lrc, plain=None)
        return None

    # --- HTTP ----------------------------------------------------------

    def _get_json(self, path: str, params: dict) -> Optional[dict]:
        """JSON или None; None означает «точно нет ответа» (для поиска — нет песен)."""
        url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            raise ProviderError("netease unreachable")

    def _search(self, title: str, artist: str) -> list[dict]:
        query = f"{title} {artist}".strip()
        data = self._get_json("/api/search/get/", {
            "s": query, "type": 1, "limit": self._limit, "offset": 0,
        })
        try:
            return data.get("result", {}).get("songs", []) or []
        except AttributeError:
            return []

    def _lyrics(self, song_id) -> Optional[str]:
        data = self._get_json("/api/song/lyric", {
            "id": song_id, "lv": 1, "kv": 1, "tv": -1,
        })
        if not data:
            return None
        lrc = (data.get("lrc") or {}).get("lyric") or ""
        return lrc.strip() or None
