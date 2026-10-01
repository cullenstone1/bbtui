from datetime import UTC, datetime, timedelta

from bbtui.text import ago, clean, one_line, relative, truncate


def test_one_line_keeps_first_line_and_drops_control_characters():
    assert one_line('Title\r\n\r\nbody') == 'Title'
    assert one_line('bell\x07 and \x1b[2Jescape') == 'bell and [2Jescape'
    assert one_line(None) == ''
    assert one_line('  \n  ') == ''


def test_clean_normalises_line_endings():
    assert clean('a\r\nb\rc\x1bd') == 'a\nb\ncd'


def test_ago():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert ago(now - timedelta(seconds=30), now) == 'now'
    assert ago(now - timedelta(hours=3), now) == '3h'
    assert ago(now - timedelta(days=45), now) == '1mo'
    assert ago(None) == ''


def test_truncate():
    assert truncate('short', 10) == 'short'
    assert truncate('abcdefghij', 5) == 'abcd…'


def test_relative():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert relative(now - timedelta(seconds=30), now) == 'just now'
    assert relative(now - timedelta(minutes=18), now) == '18 min ago'
    assert relative(now - timedelta(hours=1), now) == '1 hour ago'
    assert relative(now - timedelta(days=3), now) == '3 days ago'
    assert relative(now - timedelta(days=14), now) == '2 weeks ago'
    assert relative(now - timedelta(days=400), now) == '1 year ago'
    assert relative(None) == ''
