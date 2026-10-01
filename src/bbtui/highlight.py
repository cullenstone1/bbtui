"""Syntax highlighting for diffs.

A diff interleaves two versions of a file, so each hunk's new side (context and added lines)
and old side (context and removed lines) are highlighted as continuous code, then mapped back
onto the diff lines. That keeps multi-line strings and comments right within a hunk.
"""

from pygments.lexer import Lexer
from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text

from bbtui.diff import FileDiff

DEFAULT_THEMES = {True: 'monokai', False: 'default'}
"""Pygments styles for dark and light app themes."""


def lexer_for(path: str) -> Lexer | None:
    try:
        return get_lexer_for_filename(path.rsplit('/', 1)[-1], stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None


def _foreground_only(style: Style | str) -> Style | str:
    """Drop the theme's background so the diff's own add/remove tint shows through."""
    if isinstance(style, str):
        return style
    return Style(color=style.color, bold=style.bold, italic=style.italic, underline=style.underline)


def highlight_lines(lines: list[str], lexer: Lexer, theme: str) -> list[Text]:
    code = '\n'.join(lines)
    text = Syntax(code, lexer, theme=theme, background_color='default').highlight(code)
    text.spans = [span._replace(style=_foreground_only(span.style)) for span in text.spans]
    text.style = ''
    pieces = text.split('\n', allow_blank=True)
    return [pieces[i] if i < len(pieces) else Text(line) for i, line in enumerate(lines)]


def highlight_diff(file_diff: FileDiff, path: str, theme: str) -> dict[int, Text]:
    """Highlighted code (without the +/-/space prefix) for each code line, by line index.
    Empty if the file type isn't recognised."""
    lexer = lexer_for(path)
    if lexer is None:
        return {}
    result: dict[int, Text] = {}
    hunks: list[list[int]] = []
    for index, line in enumerate(file_diff.lines):
        if line.kind == 'hunk' or not hunks:
            hunks.append([])
        if line.kind in ('context', 'added', 'removed'):
            hunks[-1].append(index)
    for hunk in hunks:
        for side in (('context', 'added'), ('context', 'removed')):
            indexes = [i for i in hunk if file_diff.lines[i].kind in side]
            if not indexes:
                continue
            code = [file_diff.lines[i].text[1:] for i in indexes]
            for index, text in zip(indexes, highlight_lines(code, lexer, theme), strict=True):
                # Context lines appear on both sides; the new side's highlighting wins.
                result.setdefault(index, text)
    return result
