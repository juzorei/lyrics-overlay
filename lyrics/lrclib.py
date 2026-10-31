"""Провайдер LRCLIB (https://lrclib.net) — открытый API без ключей.

Используем /api/get (точный матч по title+artist+duration), при неудаче —
/api/search (fuzzy). Ответ содержит syncedLyrics (LRC) и plainLyrics.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from lyrics.provider import LyricResult, ProviderError

BASE = "https://lrclib.net"
UA = "lyrics-overlay/0.1 (github.com/you/lyrics-overlay)"


class LrclibProvider:
    name = "lrclib"

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def fetch(self, title: str, artist: str, duration_s: Optional[float],
              show_plain: bool) -> Optional[LyricResult]:
        # exact с duration -> exact без duration -> fuzzy search.
        # Перегруженный LRCLIB иногда 503-ит запросы с duration — отсюда вторая попытка.
        attempts: list = [self._get_exact]
        if duration_s:
            attempts.append(lambda t, a, d, sp: self._get_exact(t, a, None, sp))
        attempts.append(self._search)
        for attempt in attempts:
            r = attempt(title, artist, duration_s, show_plain)
            if r is not None:
                return r
        return None

    # --- HTTP helpers -------------------------------------------------

    def _request_json(self, path: str, params: dict) -> Optional[dict]:
        """GET с ретраями: LRCLIB регулярно отвечает 503 ServerOverloaded
        с заголовком retry-after. Возвращает None при исчерпании ретраев,
        ProviderError — если неисправность сетевая (не «текста нет»)."""
        url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
        last_network_error = False
        for attempt in range(4):
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 503 and attempt < 3:
                    # retry-after бывает HTTP-date; капим капитул задержки
                    try:
                        delay = min(float(e.headers.get("retry-after", 1 + attempt)), 5.0)
                    except ValueError:
                        delay = 1 + attempt
                    time.sleep(delay)
                    last_network_error = True
                    continue
                if e.code == 404:
                    return None  # трека точно нет в базе
                last_network_error = e.code >= 500
                return None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                last_network_error = True
                if attempt < 3:
                    time.sleep(1 + attempt)
                    continue
                break  # out of loop -> raise ProviderError below
        if last_network_error:
            raise ProviderError("lrclib unreachable")
        return None

    def _get_exact(self, title: str, artist: str, duration_s: Optional[float],
                   show_plain: bool) -> Optional[LyricResult]:
        params = {
            "track_name": _clean(title),
            "artist_name": _clean(artist),
        }
        if duration_s:
            params["duration"] = round(duration_s)
        data = self._request_json("/api/get", params)
        if not isinstance(data, dict) or "id" not in data:
            return None  # 404 (нет трека) и ошибки сети сходятся тут
        return _from_api(data, show_plain)

    def _search(self, title: str, artist: str, duration_s: Optional[float],
                show_plain: bool) -> Optional[LyricResult]:
        params = {"q": f"{_clean(title)} {_clean(artist)}".strip()}
        data = self._request_json("/api/search", params)
        if not isinstance(data, list) or not data:
            return None
        # первый подходящий: приоритет записям с synced, близкими по длительности
        candidates = sorted(
            data,
            key=lambda d: (
                not d.get("syncedLyrics"),
                abs((d.get("duration") or 0) - (duration_s or 0)),
            ),
        )
        return _from_api(candidates[0], show_plain)


def _clean(s: str) -> str:
    """Чистит одну строку (title или artist) для запроса к API."""
    import re

    s = re.sub(r"\((?:feat\.?|ft\.?|with)\.[^)]*\)", "", s, flags=re.I)
    s = re.sub(r"\[(?:feat\.?|ft\.?|with)\.[^\]]*\]", "", s, flags=re.I)
    s = re.sub(
        r"\s+[-–—]\s+(?:remaster(?:ed)?|radio edit|deluxe|single version|\d{4}\s+remaster(?:ed)?)\b.*",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"\s*[-–—]\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _from_api(data: dict, show_plain: bool) -> Optional[LyricResult]:
    synced = data.get("syncedLyrics") or None
    plain = data.get("plainLyrics") or None
    if synced or (show_plain and plain):
        return LyricResult(synced=synced, plain=plain)
    return None
