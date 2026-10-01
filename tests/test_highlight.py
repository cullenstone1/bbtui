from rich.style import Style

from bbtui.diff import parse_diff
from bbtui.highlight import highlight_diff, lexer_for
from bbtui.widgets.diff_view import TINTS, code_body, diff_line_text, has_full_colour

DIFF = """\
diff --git a/src/a.cpp b/src/a.cpp
--- a/src/a.cpp
+++ b/src/a.cpp
@@ -1,4 +1,4 @@
 /* a comment
-   old words */
+   new words */
 int x = 1;
"""


def color_at(text, offset: int):
    """The foreground colour applied at `offset` in a highlighted line."""
    colors = [
        span.style.color
        for span in text.spans
        if span.start <= offset < span.end and isinstance(span.style, Style) and span.style.color
    ]
    return colors[-1] if colors else None


def test_lexer_by_file_name():
    assert lexer_for('src/apps/x.hpp').name == 'C++'
    assert lexer_for('CMakeLists.txt').name == 'CMake'
    assert lexer_for('README') is None


def test_each_side_is_highlighted_as_continuous_code():
    file_diff = parse_diff(DIFF)[0]
    highlighted = highlight_diff(file_diff, 'src/a.cpp', 'monokai')
    # Lines: hunk, ` /* a comment`, `-   old words */`, `+   new words */`, ` int x = 1;`
    assert set(highlighted) == {1, 2, 3, 4}
    comment = color_at(highlighted[1], 0)
    # The removed and added lines continue the comment opened on the context line.
    assert color_at(highlighted[2], 3) == comment
    assert color_at(highlighted[3], 3) == comment
    # After the comment closes, `int` is a keyword, not comment-coloured.
    assert color_at(highlighted[4], 0) not in (None, comment)
    # The theme's background is dropped so the diff's tint shows.
    assert all(
        not isinstance(span.style, Style) or span.style.bgcolor is None
        for text in highlighted.values()
        for span in text.spans
    )


def test_unknown_file_types_are_left_to_add_remove_colours():
    assert highlight_diff(parse_diff(DIFF)[0], 'notes', 'monokai') == {}


def test_changed_lines_are_tinted_and_padded():
    file_diff = parse_diff(DIFF)[0]
    added = file_diff.lines[3]
    body = code_body(added, None)
    text = diff_line_text(added, 1, body, width=20, dark=True)
    assert text.plain == '  2 +   new words */' + ' ' * (20 - len('   new words */'))
    tint = TINTS[True]['added']
    assert any(span.style == tint and span.end == len(text.plain) for span in text.spans)
    context = diff_line_text(file_diff.lines[4], 1, code_body(file_diff.lines[4], None), 20)
    assert not any(span.style in TINTS[True].values() for span in context.spans)


def test_sixteen_colour_terminals_get_no_tints():
    assert has_full_colour('truecolor') and has_full_colour('256')
    assert not has_full_colour('standard') and not has_full_colour(None)
    added = parse_diff(DIFF)[0].lines[3]
    text = diff_line_text(added, 1, code_body(added, None), width=20, tint=False)
    assert text.plain == '  2 +   new words */'  # No padding either.
    assert not any(span.style in TINTS[True].values() for span in text.spans)
    # The code itself is green, as before highlighting existed.
    assert any(span.style == 'green' for span in text.spans)
