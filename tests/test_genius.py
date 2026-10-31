"""Тесты GeniusProvider: API-поиск по токену, скрапинг страницы,
извлечение текста из Lyrics__Container. Всё со стабом сети."""

import json
import urllib.error
from unittest.mock import patch

import pytest

from lyrics.genius import GeniusProvider, _extract_lyrics
from lyrics.provider import ProviderError


def _resp(data, code=200):
    class R:
        def __init__(self, d):
            self._d = d if isinstance(d, (bytes, str)) else json.dumps(d).encode("utf-8")

        def read(self):
            return self._d if isinstance(self._d, bytes) else self._d.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return R(data)


SEARCH_OK = {
    "response": {
        "hits": [
            {"result": {"path": "/Song-lyrics", "primary_artist": {"name": "The Artist"}}},
        ]
    }
}

SEARCH_WRONG_ARTIST = {
    "response": {
        "hits": [
            {"result": {"path": "/Cover-lyrics", "primary_artist": {"name": "Someone Else"}}},
            {"result": {"path": "/Song-lyrics", "primary_artist": {"name": "The Artist"}}},
        ]
    }
}

PAGE_HTML = (
    "<html><body>"
    '<div class="Lyrics__Container">la la la<br>second line</div>'
    '<div class="Lyrics__Container">final</div>'
    "</body></html>"
)

# реальная структура genius.com: контейнеры с ВЛОЖЕННЫМИ div-ами
NESTED_PAGE_HTML = (
    "<html><body>"
    '<div class="Lyrics__Container">[Chorus]'
    '<div class="SomeWidget">ui noise</div>'
    "la la la<br>second line</div>"
    '<div class="Lyrics__Container">final</div>'
    "</body></html>"
)

CLOUDFLARE_CHALLENGE = (
    "<html><body><script>challenge-platform</script>"
    "<title>Just a moment...</title></body></html>"
)


class TestGeniusProvider:
    def test_token_required(self):
        with pytest.raises(ValueError):
            GeniusProvider(access_token="")

    def test_search_and_scrape(self):
        p = GeniusProvider("TOKEN")

        def fake(req, timeout=None):
            url = req.full_url
            if "api.genius.com" in url:
                assert req.get_header("Authorization") == "Bearer TOKEN"
                return _resp(SEARCH_OK)
            assert "genius.com/Song-lyrics" in url
            return _resp(PAGE_HTML)

        with patch("urllib.request.urlopen", side_effect=fake):
            r = p.fetch("Song", "Artist", 100.0, True)
        assert r is not None and r.plain
        assert r.synced is None
        assert "la la la" in r.plain and "second line" in r.plain and "final" in r.plain

    def test_plain_disabled_skips_genius(self):
        p = GeniusProvider("TOKEN")
        with patch("urllib.request.urlopen") as up:  # не должен зваться вовсе
            assert p.fetch("Song", "Artist", 100.0, False) is None
            up.assert_not_called()

    def test_empty_hits_is_definitive_none(self):
        p = GeniusProvider("TOKEN")
        with patch("urllib.request.urlopen", return_value=_resp({"response": {"hits": []}})):
            assert p.fetch("Song", "Artist", 100.0, True) is None

    def test_api_down_raises_provider_error(self):
        p = GeniusProvider("TOKEN")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            with pytest.raises(ProviderError):
                p.fetch("Song", "Artist", 100.0, True)


class TestExtractLyrics:
    def test_html_to_text(self):
        assert _extract_lyrics(PAGE_HTML) == "la la la\nsecond line\nfinal"

    def test_nested_divs_not_truncated(self):
        """Регрессия: вложенный <div> внутри контейнера не обрывает текст."""
        out = _extract_lyrics(NESTED_PAGE_HTML)
        assert out is not None
        assert "[Chorus]" in out
        assert "la la la" in out and "second line" in out  # текст ПОСЛЕ вложенного div
        assert "final" in out

    def test_cloudflare_challenge_yields_none(self):
        """Challenge-страница не должна трактоваться как «текст есть»."""
        assert _extract_lyrics(CLOUDFLARE_CHALLENGE) is None

    def test_wrong_artist_skipped(self):
        """Кавер с чужим primary_artist отсеивается."""
        p = GeniusProvider("TOKEN")

        def fake(req, timeout=None):
            url = req.full_url
            if "api.genius.com" in url:
                return _resp(SEARCH_WRONG_ARTIST)
            assert "Song-lyrics" in url  # первый хит (cover) не должен скрапиться
            return _resp(PAGE_HTML)

        with patch("urllib.request.urlopen", side_effect=fake):
            r = p.fetch("Song", "The Artist", 100.0, True)
        assert r is not None and "la la la" in r.plain

    def test_no_container(self):
        assert _extract_lyrics("<html>ничего</html>") is None

    def test_entities_unescaped(self):
        html = '<div class="Lyrics__Container">a &amp; b &lt;3</div>'
        assert _extract_lyrics(html) == "a & b <3"

    def test_excess_newlines_collapsed(self):
        html = '<div class="Lyrics__Container">x<br><br><br>y</div>'
        assert _extract_lyrics(html) == "x\n\ny"
