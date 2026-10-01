"""Pipeline step logs: decoding streamed bytes into lines, sanitising them (colours are kept,
every other escape and control sequence is dropped), and spotting likely failure lines."""

import codecs
import re

from rich.text import Text

SGR = re.compile(r'\x1b\[[0-9;]*m')
"""Colour/style escapes, which are kept."""
OTHER_ESCAPES = re.compile(
    r'\x1b(?!\[[0-9;]*m)(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])'
)
"""Cursor movement, screen clearing, titles, ...: anything that isn't SGR."""

FAILURE = re.compile(
    r'\[FAIL(?:ED)?\]'
    r'|\bFAILED\b'
    r'|\b(?:[Ee]rror|[Ff]atal|ERROR|FATAL):'
    r'|make(?:\[\d+\])?: \*\*\*'
    r'|^Traceback \(most recent call last\)'
    r'|\bexit (?:code|status) [1-9]'
    r'|\bAssertionError\b'
    r'|\b[Bb]uild failed\b'
)
"""Likely failure lines. Case matters: `FAILED` is a verdict, `0 tests failed` is not."""
NOT_FAILURE = re.compile(
    r'^\s*(?:WARN|WARNING)\b|\bwarning:|\b0 (?:tests )?failed\b', re.IGNORECASE
)


def sanitize(line: str) -> str:
    """Keep colours; drop other escapes and control characters. A carriage return (progress
    output) keeps only what was written last."""
    if '\r' in line:
        line = line.rsplit('\r', 1)[-1] or line.replace('\r', '')
    line = OTHER_ESCAPES.sub('', line)
    return ''.join(c for c in line if c.isprintable() or c in '\t\x1b')


def plain(line: str) -> str:
    """`line` without colour escapes."""
    return SGR.sub('', line)


def styled(line: str) -> Text:
    text = Text.from_ansi(line, no_wrap=True, end='')
    text.expand_tabs(8)
    return text


def is_failure(line: str) -> bool:
    text = plain(line)
    return bool(FAILURE.search(text)) and not NOT_FAILURE.search(text)


class LineDecoder:
    """Turns a byte stream (a log fetched in chunks) into complete, sanitised lines."""

    def __init__(self):
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self._partial = ''

    def feed(self, data: bytes) -> list[str]:
        text = self._partial + self._decoder.decode(data)
        *lines, self._partial = text.split('\n')
        return [sanitize(line) for line in lines]

    def finish(self) -> list[str]:
        """Flush a final line without a trailing newline."""
        rest = self._partial + self._decoder.decode(b'', final=True)
        self._partial = ''
        return [sanitize(rest)] if rest else []
