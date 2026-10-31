"""Провайдер Genius (genius.com) — API-поиск по токену + скрапинг текста.

Genius так устроен: официальный API (api.genius.com) по бесплатному токену
отдаёт только метаданные и URL страницы, а сам текст живёт на web-странице.

Токен: https://genius.com/api-clients -> New API Client -> Generate Access
Token (client access token). Кладится в config.toml:

    [lyrics]
    genius_token = "ваш_токен"

Даёт только plain-текст (без таймкодов): подключается третьим в цепочку
после LRCLIB и NetEase, которые умеют synced.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Optional

from lyrics.provider import LyricResult, ProviderError

API = "https://api.genius.com"
WEB = "https://genius.com"
# genius.com за Cloudflare: простые UA получает 403-challenge;
# полный браузерный профиль заголовков проходит анонимно (проверено живьём)
PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}
API_UA = "lyrics-overlay/0.1"


class GeniusProvider:
    name = "genius"

    def __init__(self, access_token: str, timeout: float = 10.0):
        if not access_token:
            raise ValueError("genius_token required")
        self._token = access_token
        self._timeout = timeout

    def fetch(self, title: str, artist: str, duration_s: Optional[float],
              show_plain: bool) -> Optional[LyricResult]:
        if not show_plain:
            return None  # Genius умеет только plain; если plain не нужен — пропускаем
        hits = self._search_hits(title, artist)
        for hit in hits:
            result = hit.get("result") or {}
            page_path = result.get("path")
            if not page_path:
                continue
            if not _hit_artist_matches(result, artist):
                continue  # кавер/чужая песня с тем же названием — не берём
            lyrics = self._scrape_lyrics(f"{WEB}{page_path}")
            if lyrics:
                return LyricResult(synced=None, plain=lyrics)
        return None

    # --- API ------------------------------------------------------------

    def _search_hits(self, title: str, artist: str) -> list[dict]:
        params = urllib.parse.urlencode({"q": f"{title} {artist}".strip()})
        req = urllib.request.Request(
            f"{API}/search?{params}",
            headers={"User-Agent": API_UA, "Authorization": f"Bearer {self._token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            raise ProviderError(f"genius api http {e.code}") from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise ProviderError("genius api unreachable") from e
        return (data.get("response") or {}).get("hits") or []

    def _scrape_lyrics(self, url: str) -> Optional[str]:
        try:
            # следим за статусом: не-200 (Cloudflare 403 и пр.) — ProviderError,
            # иначе challenge-страница «утонет» как «текста нет»
            req = urllib.request.Request(url, headers=PAGE_HEADERS)
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                if getattr(resp, "status", 200) != 200:
                    raise ProviderError(f"genius page http {resp.status}")
                html = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise ProviderError(f"genius page http {e.code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ProviderError("genius page unreachable") from e
        return _extract_lyrics(html) or None


_DIV_OPEN_RE = re.compile(r'<div\b[^>]*class="[^"]*Lyrics__Container[^"]*"[^>]*>')
_TAG_DIV_OPEN = re.compile(r"<div\b")
_TAG_DIV_CLOSE = re.compile(r"</div>")
_BREAK_RE = re.compile(r"<br\s*/?>")
_TAG_RE = re.compile(r"<[^>]+>")
_SECTION_HEADER_RE = re.compile(r"^\s*\[\d+\]\s*$", re.M)  # «[271 Contributors]»-шум


def _hit_artist_matches(result: dict, artist: str) -> bool:
    """Совпадение по основному исполнителю хита; пустой запрос/нет данных — любое."""
    if not artist:
        return True
    primary = ((result.get("primary_artist") or {}).get("name") or "").strip()
    if not primary:
        return True
    from lyrics.parser import normalize_query

    # normalize_query("x","") даёт чистый "x" — используем как строковый нормализатор
    return normalize_query(artist, "") in normalize_query(primary, "")


def _containers(html: str) -> list[str]:
    """Содержимое div'ов Lyrics__Container с учётом вложенности.

    Реальные страницы Genius содержат вложенные <div> внутри контейнера —
    жадный регекс обрезал бы текст на первом закрытии.
    """
    out: list[str] = []
    i = 0
    while (m := _DIV_OPEN_RE.search(html, i)):
        depth, j = 1, m.end()
        while depth:
            open_m = _TAG_DIV_OPEN.search(html, j)
            close_m = _TAG_DIV_CLOSE.search(html, j)
            if not close_m:  # непарный HTML — до конца строки
                j = len(html)
                break
            if open_m and open_m.start() < close_m.start():
                depth += 1
                j = open_m.end()
            else:
                depth -= 1
                j = close_m.end()
        out.append(html[m.end(): j - len("</div>")])
        i = j
    return out


def _extract_lyrics(html: str) -> Optional[str]:
    """Текст из Lyrics__Container: <br> → \\n, теги срезаются, энтити декодируются.

    Первый контейнер на реальных страницах — служебный («271 Contributors,
    Translations…») — отбрасываем, если в нём нет упоминаний [Verse]/[Chorus].
    """
    containers = _containers(html)
    if not containers:
        return None

    def clean(inner: str) -> str:
        text = _BREAK_RE.sub("\n", inner)
        text = unescape(_TAG_RE.sub("", text))
        return text.strip()

    texts = [clean(c) for c in containers]
    texts = [t for t in texts if t]
    # служебный заголовок Genius бывает склеен в одну строку:
    # «1 ContributorTOMA DE NOVO LyricsLETRA DE "TOMA DE NOVO"»,
    # при этом текст песни начинается со следующей строки — дропаем
    # строки-хвосты, содержащие Contributor-маркер (макс. 2 первых)
    if texts:
        head = texts[0].splitlines()
        cleaned_head = [l for l in head if "Contribut" not in l or len(l) > 130]
        # строка >130 симв. — это уже текст, не заголовок
        texts[0] = "\n".join(cleaned_head).strip()
        if not texts[0]:
            texts = texts[1:]
    if not texts:
        return None
    joined = re.sub(r"\n{3,}", "\n\n", "\n".join(texts))
    return joined.strip() or None
    for inner in _containers(html):
        text = _BREAK_RE.sub("\n", inner)
        text = unescape(_TAG_RE.sub("", text))
        text = _SECTION_HEADER_RE.sub("", text)
        text = text.strip()
        if text:
            chunks.append(text)
    if not chunks:
        return None
    joined = re.sub(r"\n{3,}", "\n\n", "\n".join(chunks))
    return joined.strip() or None

