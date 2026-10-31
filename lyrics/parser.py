"""Парсер LRC-текста и поиск активной строки по таймингу.

LRCLIB отдаёт вроде обычного текста с метками [mm:ss.xx] в начале строк
(extended LRC тоже поддерживаем на всякий случай — <mm:ss.xx> внутри).
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

LINE_TAGS = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
INLINE_TAGS = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")

_EXTRA_NOISE = re.compile(
    r"(?i)\b(official\s+(?:music\s+)?video|lyric\s+video|hd|hq|\d{4}\s*remaster(?:ed)?)\b"
)


@dataclass(frozen=True)
class LyricLine:
    time_s: float
    text: str


def normalize_query(title: str, artist: str) -> str:
    """Чистит название/исполнителя для кэш-ключа и поисковых запросов."""
    def clean(s: str) -> str:
        s = s.strip()
        s = re.sub(r"\s+[-–—]\s+(?:remaster(?:ed)?|radio edit|deluxe|single version|\d{4}\s+remaster(?:ed)?)\b.*", "", s, flags=re.I)
        s = re.sub(r"\s*[-–—]\s*$", "", s)  # висячий дефис после чистки
        s = re.sub(r"\((?:feat\.?|ft\.?|with)\.[^)]*\)", "", s, flags=re.I)
        s = re.sub(r"\[(?:feat\.?|ft\.?|with)\.[^\]]*\]", "", s, flags=re.I)
        s = _EXTRA_NOISE.sub(" ", s)
        return re.sub(r"\s+", " ", s).strip().lower()

    title_c = clean(title)
    artist_c = clean(artist.split(",")[0] if artist else "")
    return f"{artist_c}::{title_c}" if artist_c else title_c


def parse_lrc(lrc_text: str) -> list[LyricLine]:
    """Парсит LRC в список строк с таймингами, отсортированный по времени."""
    lines: list[tuple[float, str]] = []

    for raw in lrc_text.splitlines():
        text_no_inline = INLINE_TAGS.sub("", raw).strip()
        matches = list(LINE_TAGS.finditer(text_no_inline))
        if not matches:
            continue
        # текст после последней метки (учитываем несколько меток на строку)
        body = text_no_inline[matches[-1].end():].strip()
        for m in matches:
            minutes = int(m.group(1))
            seconds = int(m.group(2))
            frac_raw = m.group(3) or "0"
            # [mm:ss.x] — десятые; [mm:ss.xxx] — тысячные
            frac = int(frac_raw) / (10 ** len(frac_raw))
            t = minutes * 60 + seconds + frac
            lines.append((t, body))

    lines.sort(key=lambda p: p[0])
    return [LyricLine(time_s=t, text=text) for t, text in lines]


def active_line_index(lines: list[LyricLine], position_s: float) -> int:
    """Индекс строки, которая сейчас звучит (-1 если ещё не начало / пусто)."""
    if not lines:
        return -1
    times = [ln.time_s for ln in lines]
    return bisect_right(times, position_s + 1e-6) - 1


__all__ = ["LyricLine", "normalize_query", "parse_lrc", "active_line_index"]
