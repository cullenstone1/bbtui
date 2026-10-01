from rich.text import Text
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.widget import Widget
from textual.widgets import Static

from bbtui.diff import DiffLine, FileDiff
from bbtui.models import DiffStat
from bbtui.text import one_line
from bbtui.widgets.comments import CommentView, Thread

MAX_FILE_LINES = 5_000

LINE_STYLES = {
    'added': 'green',
    'removed': 'red',
    'hunk': 'bold cyan',
    'meta': 'yellow',
    'note': 'dim italic',
    'context': '',
}


def diff_line_text(line: DiffLine, gutter: int) -> Text:
    text = Text(no_wrap=True, overflow='ignore')
    old = '' if line.old is None else str(line.old)
    new = '' if line.new is None else str(line.new)
    text.append(f'{old:>{gutter}} {new:>{gutter}} ', style='dim')
    text.append(line.text, style=LINE_STYLES[line.kind])
    text.expand_tabs(4)
    return text


class DiffView(ScrollableContainer):
    """One file's diff, with its inline comment threads under the lines they refer to."""

    BINDINGS = [Binding('escape', 'leave', 'Files')]

    def action_leave(self) -> None:
        """Return focus to the file chooser, the widget before this one."""
        self.screen.focus_previous()

    async def show_file(
        self,
        stat: DiffStat,
        file_diff: FileDiff | None,
        threads: list[Thread],
        names: dict[str, str],
    ) -> None:
        self.border_title = Text(one_line(stat.path))
        self.border_subtitle = f'+{stat.lines_added} −{stat.lines_removed}'
        widgets: list[Widget] = []

        def add_thread(thread: Thread) -> None:
            widgets.extend(CommentView(c, depth, names, show_location=False) for c, depth in thread)

        if file_diff is None:
            widgets.append(Static(Text('No diff for this file.', style='dim italic')))
            for thread in threads:
                add_thread(thread)
            await self._replace(widgets)
            return

        lines = file_diff.lines[:MAX_FILE_LINES]
        anchored: dict[int, list[Thread]] = {}
        for thread in threads:
            root = thread[0][0]
            index = file_diff.line_index(root.line_to, root.line_from)
            if index is None or index >= len(lines):
                # File-level comments, and comments on lines no longer in the diff.
                add_thread(thread)
            else:
                anchored.setdefault(index, []).append(thread)

        numbers = [n for line in lines for n in (line.old, line.new) if n is not None]
        gutter = len(str(max(numbers, default=0)))
        segment: list[Text] = []

        def flush() -> None:
            if segment:
                widgets.append(Static(Text('\n', no_wrap=True).join(segment), classes='diff-lines'))
                segment.clear()

        for index, line in enumerate(lines):
            segment.append(diff_line_text(line, gutter))
            if index in anchored:
                flush()
                for thread in anchored[index]:
                    add_thread(thread)
        if len(file_diff.lines) > MAX_FILE_LINES:
            segment.append(Text(f'… truncated at {MAX_FILE_LINES} lines', style='dim italic'))
        flush()
        if not lines:
            widgets.append(Static(Text('No textual changes.', style='dim italic')))
        await self._replace(widgets)

    async def _replace(self, widgets: list[Widget]) -> None:
        await self.remove_children()
        await self.mount_all(widgets)
        self.scroll_home(animate=False)
