import pytest

from lyrics.parser import active_line_index, normalize_query, parse_lrc


class TestParseLrc:
    def test_basic(self):
        lrc = "[00:01.50] первая\n[00:05.00] вторая\n[00:10]третья"
        lines = parse_lrc(lrc)
        assert [(l.time_s, l.text) for l in lines] == [
            (1.5, "первая"),
            (5.0, "вторая"),
            (10.0, "третья"),
        ]

    def test_multiple_tags_one_line(self):
        lines = parse_lrc("[00:01.00][00:20.00] припев")
        assert len(lines) == 2
        assert lines[0].time_s == 1.0
        assert lines[1].time_s == 20.0
        assert lines[0].text == lines[1].text == "припев"

    def test_inline_tags_stripped(self):
        lines = parse_lrc("[00:01.00] сло<00:01.50>во")
        assert lines[0].text == "слово"

    def test_sorted_by_time(self):
        lines = parse_lrc("[00:10.00] b\n[00:02.00] a")
        assert [l.text for l in lines] == ["a", "b"]

    def test_metadata_and_empty_lines_ignored(self):
        lines = parse_lrc("[ti:Title]\n[ar:Artist]\n\n[00:01.00] лишь одна")
        assert len(lines) == 1
        assert lines[0].text == "лишь одна"

    def test_empty(self):
        assert parse_lrc("") == []


class TestActiveLine:
    def test_before_first(self):
        lines = parse_lrc("[00:05.00] a\n[00:10.00] b")
        assert active_line_index(lines, 0.0) == -1
        assert active_line_index(lines, 4.99) == -1

    def test_exact_boundary(self):
        lines = parse_lrc("[00:05.00] a\n[00:10.00] b")
        assert active_line_index(lines, 5.0) == 0
        assert active_line_index(lines, 10.0) == 1

    def test_between(self):
        lines = parse_lrc("[00:05.00] a\n[00:10.00] b")
        assert active_line_index(lines, 7.3) == 0

    def test_after_end(self):
        lines = parse_lrc("[00:05.00] a")
        assert active_line_index(lines, 999.0) == 0


class TestNormalize:
    def test_feat_paren_removed(self):
        assert normalize_query("Song (feat. Bob)", "Alice") == "alice::song"

    def test_feat_square_removed(self):
        assert normalize_query("Song [ft. Bob]", "Alice") == "alice::song"

    def test_remaster_removed(self):
        assert normalize_query("Song - 2011 Remaster", "Alice") == "alice::song"

    def test_no_artist(self):
        assert normalize_query("Just a Song", "") == "just a song"

    def test_case_and_spaces(self):
        assert normalize_query("  SoNg  Name ", "Artist") == "artist::song name"

    def test_extra_artist_kept_in_key(self):
        # только первый исполнитель участвует в ключе
        assert normalize_query("S", "Alice, Bob") == "alice::s"
