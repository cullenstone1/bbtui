from dataclasses import dataclass

from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.geometry import Region
from textual.widget import Widget
from textual.widgets import Static

from bbtui.diff import DiffLine, FileDiff
from bbtui.highlight import DEFAULT_THEMES, highlight_diff
from bbtui.models import DiffStat
from bbtui.text import one_line
from bbtui.widgets.comments import Thread, thread_views

MAX_FILE_LINES = 5_000

LINE_STYLES = {
    'added': 'green',
    'removed': 'red',
    'hunk': 'bold cyan',
    'meta': 'yellow',
    'note': 'dim italic',
    'context': '',
}
CODE_KINDS = ('added', 'removed', 'context')


TINTS = {
    True: {'added': Style(bgcolor='#12301c'), 'removed': Style(bgcolor='#3a1518')},
    False: {'added': Style(bgcolor='#dafbe1'), 'removed': Style(bgcolor='#ffebe9')},
}
"""Backgrounds for added/removed lines, for dark and light themes."""
MARKER_STYLES = {'added': 'bold green', 'removed': 'bold red', 'context': 'dim'}


def has_full_colour(color_system: str | None) -> bool:
    """Whether the terminal can show subtle background tints and syntax colours. With only 16
    colours they collapse to black/grey, so diffs fall back to green/red text instead."""
    return color_system in ('256', 'truecolor')


def code_body(line: DiffLine, highlighted: Text | None) -> Text:
    """A code line's content without its +/-/space prefix: syntax highlighted when possible,
    else coloured by kind."""
    body = highlighted.copy() if highlighted is not None else None
    if body is None:
        body = Text(line.text[1:], style=LINE_STYLES[line.kind])
    body.no_wrap = True
    body.expand_tabs(4)
    return body


def diff_line_text(
    line: DiffLine,
    gutter: int,
    body: Text | None = None,
    width: int = 0,
    dark: bool = True,
    tint: bool = True,
) -> Text:
    """One diff line: line numbers, then the content. Code lines get their +/- marker and, for
    added/removed lines, a background tint padded to `width` so the tint forms a block."""
    text = Text(no_wrap=True, overflow='ignore')
    old = '' if line.old is None else str(line.old)
    new = '' if line.new is None else str(line.new)
    text.append(f'{old:>{gutter}} {new:>{gutter}} ', style='dim')
    if line.kind not in CODE_KINDS:
        content = Text(line.text, style=LINE_STYLES[line.kind])
        content.expand_tabs(4)
        text.append_text(content)
        return text
    start = len(text)
    text.append(line.text[:1] or ' ', style=MARKER_STYLES[line.kind])
    body = body if body is not None else code_body(line, None)
    text.append_text(body)
    if not tint:
        return text
    if padding := width - body.cell_len:
        text.append(' ' * padding)
    if style := TINTS[dark].get(line.kind):
        text.stylize(style, start)
    return text


@dataclass(frozen=True)
class LineTarget:
    """Where an inline comment on the cursor line goes."""

    path: str
    line_to: int | None
    line_from: int | None
    text: str

    @property
    def number(self) -> int | None:
        return self.line_to or self.line_from


class DiffLines(Static):
    """A run of consecutive diff lines (between comment threads), with an optional cursor."""

    def __init__(self, texts: list[Text], start: int):
        self.texts = texts
        self.start = start
        self.cursor: int | None = None
        super().__init__(self._paint(), classes='diff-lines')

    @property
    def end(self) -> int:
        return self.start + len(self.texts)

    def _paint(self) -> Text:
        lines = list(self.texts)
        if self.cursor is not None:
            lines[self.cursor] = lines[self.cursor].copy()
            lines[self.cursor].stylize('reverse')
        return Text('\n', no_wrap=True).join(lines)

    def set_cursor(self, relative: int | None) -> None:
        if relative != self.cursor:
            self.cursor = relative
            self.update(self._paint())

    def on_click(self, event: events.Click) -> None:
        view = self.parent
        if isinstance(view, DiffView) and 0 <= event.y < len(self.texts):
            view.focus()
            view.move_cursor(self.start + event.y, scroll=False)


class DiffView(ScrollableContainer):
    """One file's diff with a line cursor, and its inline comment threads under the lines they
    refer to."""

    BINDINGS = [
        Binding('escape', 'leave', 'Files'),
        Binding('up,k', 'cursor(-1)', 'Up', show=False),
        Binding('down,j', 'cursor(1)', 'Down', show=False),
        Binding('h', 'scroll_left', 'Left', show=False),
        Binding('l', 'scroll_right', 'Right', show=False),
        Binding('pageup', 'page(-1)', 'Page up', show=False),
        Binding('pagedown', 'page(1)', 'Page down', show=False),
        Binding('home,g', 'jump(0)', 'Top', show=False),
        Binding('end,G', 'jump(-1)', 'Bottom', show=False),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stat: DiffStat | None = None
        self.lines: list[DiffLine] = []
        self.segments: list[DiffLines] = []
        self.cursor: int | None = None

    @property
    def cursor_line(self) -> DiffLine | None:
        return self.lines[self.cursor] if self.cursor is not None else None

    def line_target(self) -> LineTarget | None:
        """The comment location for the cursor line, or None if it isn't a code line."""
        line = self.cursor_line
        if self.stat is None or line is None or line.kind not in CODE_KINDS:
            return None
        path = self.stat.new_path or self.stat.old_path or ''
        if line.kind == 'removed':
            return LineTarget(path, None, line.old, line.text)
        return LineTarget(path, line.new, None, line.text)

    def _segment(self, index: int) -> DiffLines | None:
        return next((s for s in self.segments if s.start <= index < s.end), None)

    def move_cursor(self, index: int, scroll: bool = True) -> None:
        if not self.lines:
            return
        index = max(0, min(index, len(self.lines) - 1))
        if self.cursor is not None and (old := self._segment(self.cursor)):
            old.set_cursor(None)
        self.cursor = index
        segment = self._segment(index)
        if segment is None:
            return
        segment.set_cursor(index - segment.start)
        if scroll:
            y = segment.virtual_region.y + index - segment.start
            self.scroll_to_region(Region(0, y, 1, 1), animate=False, immediate=True)

    def action_cursor(self, step: int) -> None:
        self.move_cursor((self.cursor or 0) + step)

    def action_page(self, step: int) -> None:
        self.move_cursor((self.cursor or 0) + step * max(1, self.scrollable_content_region.height))

    def action_jump(self, index: int) -> None:
        self.move_cursor(index if index >= 0 else len(self.lines) - 1)

    def action_leave(self) -> None:
        """Return focus to the file chooser, the widget before this one."""
        self.screen.focus_previous()

    async def show_file(
        self,
        stat: DiffStat,
        file_diff: FileDiff | None,
        threads: list[Thread],
        names: dict[str, str],
        keep_position: bool = False,
    ) -> None:
        """Show one file. With `keep_position`, the cursor and scroll position are kept (for
        re-rendering the same file, e.g. after a comment is posted)."""
        cursor, scroll_y = self.cursor, self.scroll_y
        self.stat = stat
        self.border_title = Text(one_line(stat.path))
        self.border_subtitle = f'+{stat.lines_added} −{stat.lines_removed}'
        self.lines = file_diff.lines[:MAX_FILE_LINES] if file_diff else []
        self.segments = []
        self.cursor = None
        widgets: list[Widget] = []

        def add_thread(thread: Thread, show_context: bool | None = None) -> None:
            widgets.extend(thread_views(thread, names, False, show_context))

        if file_diff is None:
            widgets.append(Static(Text('No diff for this file.', style='dim italic')))
            for thread in threads:
                add_thread(thread, show_context=True)
            await self._replace(widgets)
            return

        anchored: dict[int, list[Thread]] = {}
        for thread in threads:
            root = thread[0][0]
            index = file_diff.line_index(root.line_to, root.line_from)
            if root.outdated or index is None or index >= len(self.lines):
                # File-level comments, and comments on lines that have since changed (their line
                # numbers refer to an older version), shown first with the code they were on.
                add_thread(thread, show_context=True)
            else:
                anchored.setdefault(index, []).append(thread)

        numbers = [n for line in self.lines for n in (line.old, line.new) if n is not None]
        gutter = len(str(max(numbers, default=0)))
        dark = self.app.current_theme.dark
        theme = getattr(self.app, 'settings', None)
        theme = (theme.syntax_theme if theme else None) or DEFAULT_THEMES[dark]
        path = stat.new_path or stat.old_path or ''
        full_colour = has_full_colour(self.app.console.color_system)
        highlighted = highlight_diff(file_diff, path, theme) if self.lines and full_colour else {}
        bodies = {
            index: code_body(line, highlighted.get(index))
            for index, line in enumerate(self.lines)
            if line.kind in CODE_KINDS
        }
        width = max((body.cell_len for body in bodies.values()), default=0)
        texts: list[Text] = []
        start = 0

        def flush(end: int) -> None:
            nonlocal start
            if texts:
                segment = DiffLines(list(texts), start)
                self.segments.append(segment)
                widgets.append(segment)
                texts.clear()
            start = end

        for index, line in enumerate(self.lines):
            texts.append(
                diff_line_text(line, gutter, bodies.get(index), width, dark, tint=full_colour)
            )
            if index in anchored:
                flush(index + 1)
                for thread in anchored[index]:
                    add_thread(thread)
        flush(len(self.lines))
        if len(file_diff.lines) > MAX_FILE_LINES:
            note = f'… truncated at {MAX_FILE_LINES} lines'
            widgets.append(Static(Text(note, style='dim italic')))
        if not self.lines:
            widgets.append(Static(Text('No textual changes.', style='dim italic')))
        await self._replace(widgets)

        if keep_position and cursor is not None:
            self.move_cursor(cursor, scroll=False)
            self.call_after_refresh(self.scroll_to, y=scroll_y, animate=False)
        else:
            first_code = next(
                (i for i, line in enumerate(self.lines) if line.kind in CODE_KINDS), 0
            )
            self.move_cursor(first_code, scroll=False)

    async def _replace(self, widgets: list[Widget]) -> None:
        await self.remove_children()
        await self.mount_all(widgets)
        self.scroll_home(animate=False)
