"""Простейший дисковый JSON-кэш ответов LRCLIB.

Ключ — normalize_query(title, artist). Значение — объект ответа:
{"synced": str|null, "plain": str|null, "notfound": bool}.
notfound тоже кэшируем (с TTL), чтобы не долбить API повторами.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

NOTFOUND_TTL_S = 7 * 24 * 3600  # неделя — вдруг текст добавят


class LyricsCache:
    def __init__(self, cache_dir: str | Path):
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        import hashlib

        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self._dir / f"{h}.json"

    def get(self, key: str, ttl_s: float = 30 * 24 * 3600) -> Optional[dict]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        age = time.time() - rec.get("ts", 0)
        ttl = NOTFOUND_TTL_S if rec.get("notfound") else ttl_s
        return rec if age < ttl else None

    def put(self, key: str, synced: Optional[str], plain: Optional[str], notfound: bool = False) -> None:
        rec = {"ts": time.time(), "synced": synced, "plain": plain, "notfound": notfound}
        tmp = self._path(key).with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(rec, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp, self._path(key))
        except OSError:
            # кэш не критичен — отказ диска не должен ломать приложение
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
