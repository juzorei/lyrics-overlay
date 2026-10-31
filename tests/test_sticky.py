"""Тесты липкости источника (SMTC/MPRIS) и строгого матча NetEase.

Липкость: пока выбранный плеер играет, браузер/видео не перебивает трек.
"""

import json
from unittest.mock import patch

from lyrics.netease import NeteaseProvider
from players.windows_smtc import SmtcMonitor


class FakePlaybackInfo:
    def __init__(self, playing):
        self.playback_status = 4 if playing else 5  # PLAYING / PAUSED


class FakeSession:
    def __init__(self, source, playing):
        self.source_app_user_model_id = source
        self._pb = FakePlaybackInfo(playing)

    def get_playback_info(self):
        return self._pb


def _monitor():
    m = SmtcMonitor.__new__(SmtcMonitor)  # без запуска потока/SMTC
    m._preferred_source = None
    return m


class TestStickySession:
    def test_prefers_playing_session(self):
        m = _monitor()
        items = [(FakeSession("Spotify", False), False), (FakeSession("Opera", True), True)]
        picked = m._pick_session(items)
        assert picked.source_app_user_model_id == "Opera"

    def test_sticky_while_playing(self):
        m = _monitor()
        m._pick_session([(FakeSession("Spotify", True), True)])
        # браузер заиграл — Spotify всё ещё играет, не переключаемся
        items = [(FakeSession("Spotify", True), True), (FakeSession("Opera", True), True)]
        assert m._pick_session(items).source_app_user_model_id == "Spotify"

    def test_switch_when_preferred_paused_and_other_playing(self):
        m = _monitor()
        m._preferred_source = "Spotify"
        items = [(FakeSession("Spotify", False), False), (FakeSession("Opera", True), True)]
        assert m._pick_session(items).source_app_user_model_id == "Opera"

    def test_keep_preferred_paused_when_nothing_else_plays(self):
        m = _monitor()
        m._preferred_source = "Spotify"
        items = [(FakeSession("Spotify", False), False), (FakeSession("Opera", False), False)]
        assert m._pick_session(items).source_app_user_model_id == "Spotify"

    def test_back_to_preferred_when_resumes(self):
        m = _monitor()
        m._preferred_source = "Spotify"  # был на паузе, переключились на Opera
        items = [(FakeSession("Spotify", True), True), (FakeSession("Opera", False), False)]
        assert m._pick_session(items).source_app_user_model_id == "Spotify"

    def test_none_when_nothing_plays_and_no_preferred(self):
        m = _monitor()
        items = [(FakeSession("Opera", False), False)]
        assert m._pick_session(items) is None


class TestNeteaseStrictMatch:
    def _fetch(self, songs, lyric, title, artist, duration):
        p = NeteaseProvider()

        def fake_urlopen(req, timeout=None):
            url = req.full_url
            if "search" in url:
                return _resp({"result": {"songs": songs}})
            return _resp({"lrc": {"lyric": lyric}})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            return p.fetch(title, artist, duration, True)

    def test_wrong_artist_rejected(self):
        songs = [{"name": "Song", "artists": [{"name": "Someone Else"}], "id": 1, "duration": 100000}]
        assert self._fetch(songs, "[00:01]x", "Song", "Real Artist", 100.0) is None

    def test_matching_artist_accepted(self):
        songs = [{"name": "Song", "artists": [{"name": "Real Artist"}], "id": 1, "duration": 100000}]
        r = self._fetch(songs, "[00:01]x", "Song", "Real Artist", 100.0)
        assert r is not None and r.synced == "[00:01]x"

    def test_wrong_duration_and_artist_rejected(self):
        songs = [{"name": "Song", "artists": [{"name": "Cover Guy"}], "id": 1, "duration": 300000}]
        assert self._fetch(songs, "[00:01]x", "Song", "Real Artist", 100.0) is None


def _resp(data):
    class R:
        def read(self):
            return json.dumps(data).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return R()
