import time

from lyrics.cache import LyricsCache


def test_put_get_roundtrip(tmp_path):
    c = LyricsCache(tmp_path / "cache")
    assert c.get("alice::song") is None
    c.put("alice::song", synced="[00:01]x", plain=None)
    rec = c.get("alice::song")
    assert rec["synced"] == "[00:01]x"
    assert rec["plain"] is None
    assert not rec["notfound"]


def test_notfound_cached_with_short_ttl(tmp_path):
    c = LyricsCache(tmp_path / "cache")
    c.put("k", None, None, notfound=True)
    rec = c.get("k")
    assert rec is not None and rec["notfound"]
    # «не найдено» протухает быстрее обычной записи
    c2 = LyricsCache(tmp_path / "cache")
    aged = {"ts": time.time() - 8 * 24 * 3600, "synced": None, "plain": None, "notfound": True}
    import json

    p = c2._path("k")
    p.write_text(json.dumps(aged), encoding="utf-8")
    assert c2.get("k") is None


def test_corrupt_file_ignored(tmp_path):
    c = LyricsCache(tmp_path / "cache")
    c._path("k").write_text("not json", encoding="utf-8")
    assert c.get("k") is None


def test_plain_only_roundtrip(tmp_path):
    c = LyricsCache(tmp_path / "cache")
    c.put("k", None, "текст")
    assert c.get("k")["plain"] == "текст"
