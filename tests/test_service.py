"""Тесты цепочки провайдеров: порядок, отказоустойчивость, кэш, notfound."""

import pytest

from lyrics.cache import LyricsCache
from lyrics.provider import LyricsService, LyricResult


class FakeProvider:
    def __init__(self, name: str, result=None, raise_exc: bool = False):
        self.name = name
        self._result = result
        self._raise = raise_exc
        self.calls = 0

    def fetch(self, title, artist, duration_s, show_plain):
        self.calls += 1
        if self._raise:
            raise RuntimeError(f"{self.name} is down")
        return self._result


SYNCED = LyricResult(synced="[00:01]la", plain=None)
PLAIN = LyricResult(synced=None, plain="la la")


def test_first_provider_wins(tmp_path):
    a = FakeProvider("a", SYNCED)
    b = FakeProvider("b", SYNCED)
    svc = LyricsService(LyricsCache(tmp_path), [a, b])
    assert svc.get_lyrics("Song", "Artist", 100.0) is not None
    assert a.calls == 1
    assert b.calls == 0  # второй не звали — первый дал результат


def test_fallback_to_second_provider(tmp_path):
    a = FakeProvider("a", None)
    b = FakeProvider("b", SYNCED)
    svc = LyricsService(LyricsCache(tmp_path), [a, b])
    r = svc.get_lyrics("Song", "Artist", 100.0)
    assert r is not None and r.synced
    assert a.calls == 1 and b.calls == 1


def test_crashing_provider_skipped(tmp_path):
    a = FakeProvider("a", raise_exc=True)
    b = FakeProvider("b", SYNCED)
    svc = LyricsService(LyricsCache(tmp_path), [a, b])
    r = svc.get_lyrics("Song", "Artist", 100.0)
    assert r is not None  # упавший источник не роняет цепочку


def test_notfound_cached(tmp_path):
    a = FakeProvider("a", None)
    svc = LyricsService(LyricsCache(tmp_path), [a])
    assert svc.get_lyrics("Song", "Artist", 100.0) is None
    assert svc.get_lyrics("Song", "Artist", 100.0) is None
    assert a.calls == 1  # второй вызов из кэша, источник не дёргаем


def test_result_cached(tmp_path):
    a = FakeProvider("a", SYNCED)
    svc = LyricsService(LyricsCache(tmp_path), [a])
    assert svc.get_lyrics("Song", "Artist", 100.0) is not None
    assert svc.get_lyrics("Song", "Artist", 100.0) is not None
    assert a.calls == 1


def test_plain_not_used_when_disabled(tmp_path):
    a = FakeProvider("a", PLAIN)
    svc = LyricsService(LyricsCache(tmp_path), [a], show_plain_when_unsynced=False)
    assert svc.get_lyrics("Song", "Artist", 100.0) is None
    # plain-результат не кэшируется как найденный even при повторе
    assert a.calls == 1


def test_plain_used_when_enabled(tmp_path):
    a = FakeProvider("a", PLAIN)
    svc = LyricsService(LyricsCache(tmp_path), [a], show_plain_when_unsynced=True)
    r = svc.get_lyrics("Song", "Artist", 100.0)
    assert r is not None and r.plain and not r.synced
