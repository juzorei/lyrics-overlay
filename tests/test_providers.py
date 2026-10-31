"""Тесты реальных провайдеров с застёганым urlopen: ловят дрейф сигнатур
(лямбда-баг с show_plain прошёл мимо FakeProvider-тестов) и семантику
ProviderError vs «текста нет»."""

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from lyrics.cache import LyricsCache
from lyrics.lrclib import LrclibProvider
from lyrics.netease import NeteaseProvider
from lyrics.provider import LyricsService, ProviderError


def _resp(payload: dict, code: int = 200):
    class R:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return R(json.dumps(payload).encode("utf-8"))


LRCLIB_SYNCED = {"id": 1, "syncedLyrics": "[00:01]la", "plainLyrics": None}
NETEASE_SONGS = {
    "result": {
        "songs": [
            {"name": "Song", "artists": [{"name": "Artist"}], "id": 42, "duration": 100000},
        ]
    }
}
NETEASE_LRC = {"lrc": {"lyric": "[00:01.00]la\n"}}


class TestLrclibProvider:
    def test_fetch_signature_and_synced_result(self):
        p = LrclibProvider()
        with patch("urllib.request.urlopen", return_value=_resp(LRCLIB_SYNCED)):
            r = p.fetch("Song", "Artist", 100.0, True)
        assert r is not None and r.synced == "[00:01]la"

    def test_fetch_duration_fallback_lambda_invoked(self):
        """Ветка exact-без-duration должна реально вызваться: первый вызов
        404, второй (без duration) — успех."""
        p = LrclibProvider()
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "duration" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
            return _resp(LRCLIB_SYNCED)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            r = p.fetch("Song", "Artist", 100.0, True)
        assert r is not None and r.synced
        assert len(calls) == 2

    def test_404_is_definitive_none(self):
        p = LrclibProvider()

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            assert p.fetch("Song", "Artist", 100.0, True) is None


class TestNeteaseProvider:
    def test_fetch_signature_and_lrc(self):
        p = NeteaseProvider()
        with patch("urllib.request.urlopen", side_effect=[_resp(NETEASE_SONGS), _resp(NETEASE_LRC)]):
            r = p.fetch("Song", "Artist", 100.0, True)
        assert r is not None and r.synced == "[00:01.00]la"  # strip() в _lyrics

    def test_duration_filter_prefers_match(self):
        """Кандидат с чужой длительностью отсеивается при наличии нужного."""
        p = NeteaseProvider()
        songs = {
            "result": {
                "songs": [
                    {"name": "Song (cover)", "artists": [{"name": "Artist"}], "id": 1, "duration": 300000},
                    {"name": "Song", "artists": [{"name": "Artist"}], "id": 2, "duration": 100000},
                ]
            }
        }
        lyric_cover = {"lrc": {"lyric": "[00:01]WRONG"}}
        lyric_real = {"lrc": {"lyric": "[00:01]RIGHT"}}

        def fake_urlopen(req, timeout=None):
            if "search" in req.full_url:
                return _resp(songs)
            if "id=1" in req.full_url:
                return _resp(lyric_cover)
            return _resp(lyric_real)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            r = p.fetch("Song", "Artist", 100.0, True)
        assert r is not None and "RIGHT" in r.synced


class TestProviderErrorNotCached:
    def test_network_error_not_cached_as_notfound(self, tmp_path):
        """Сбой сети у единственного провайдера не должен кэшировать notfound."""
        import lyrics.lrclib as lrclib_mod

        svc = LyricsService(LyricsCache(tmp_path), [LrclibProvider()])
        down = patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down"))
        # ретраи спят 1+2+3 c — подменяем sleep в модуле провайдера
        with patch.object(lrclib_mod.time, "sleep", lambda s: None), down:
            assert svc.get_lyrics("Song", "Artist", 100.0) is None

        # кэш не должен содержать notfound-запись
        assert svc._cache.get("artist::song") is None

        # а когда сеть вернулась — текст находится (не отравлено кэшем)
        with patch("urllib.request.urlopen", return_value=_resp(LRCLIB_SYNCED)):
            r = svc.get_lyrics("Song", "Artist", 100.0)
        assert r is not None and r.synced
