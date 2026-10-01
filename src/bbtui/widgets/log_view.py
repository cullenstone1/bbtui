from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from bbtui.logs import is_failure, plain, styled

CACHE_SIZE = 2_000
FAILURE_MARK = Style(color='red', bold=True)
CURRENT_LINE = Style(reverse=True)


class LogView(ScrollView, can_focus=True):
    """A large log, drawn a line at a time (only visible lines are rendered), with a line
    number gutter, failure markers, a highlighted current line, and search."""

    BINDINGS = [
        Binding('e', 'failure(1)', 'Next failure'),
        Binding('E', 'failure(-1)', 'Prev failure'),
        Binding('n', 'match(1)', 'Next match', show=False),
        Binding('N', 'match(-1)', 'Prev match', show=False),
        Binding('g,home', 'top', 'Top', show=False),
        Binding('G,end', 'bottom', 'Bottom', show=False),
        Binding('j', 'scroll_down', show=False),
        Binding('k', 'scroll_up', show=False),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.lines: list[str] = []
        self.failures: list[int] = []
        self.matches: list[int] = []
        self.search_text = ''
        self.current: int | None = None
        self._width = 0
        self._cache: dict[int, Text] = {}

    # --- content ----------------------------------------------------------------------------------

    def clear(self) -> None:
        self.lines, self.failures, self.matches = [], [], []
        self.current = None
        self._width = 0
        self._cache.clear()
        self.virtual_size = Size(0, 0)
        self.scroll_to(0, 0, animate=False)
        self.refresh()

    def append(self, lines: list[str]) -> None:
        """Add lines; if the view was at the bottom, it stays there (follows the log)."""
        if not lines:
            return
        following = self.is_vertical_scroll_end or not self.lines
        start = len(self.lines)
        self.lines.extend(lines)
        for offset, line in enumerate(lines):
            index = start + offset
            if is_failure(line):
                self.failures.append(index)
            if self.search_text and self.search_text in plain(line).lower():
                self.matches.append(index)
            self._width = max(self._width, cell_len(plain(line)))
        self.virtual_size = Size(self._gutter_width + self._width, len(self.lines))
        if following:
            # After the refresh, so the scroll range already includes the new lines.
            self.call_after_refresh(self.scroll_end, animate=False)
        self.refresh()

    @property
    def _gutter_width(self) -> int:
        return len(str(max(len(self.lines), 1))) + 3

    # --- rendering --------------------------------------------------------------------------------

    def _text(self, index: int) -> Text:
        if (text := self._cache.get(index)) is None:
            if len(self._cache) > CACHE_SIZE:
                self._cache.clear()
            text = self._cache[index] = styled(self.lines[index])
        return text

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        index = scroll_y + y
        width = self.scrollable_content_region.width
        if index >= len(self.lines):
            return Strip.blank(width, self.rich_style)
        gutter_width = self._gutter_width
        number = str(index + 1).rjust(gutter_width - 3)
        failed = index in self._failure_set
        gutter = [
            Segment(number, Style(dim=True)),
            Segment(' ● ' if failed else '   ', FAILURE_MARK if failed else None),
        ]
        line = Strip(gutter + list(self._text(index).render(self.app.console)))
        line = line.crop(scroll_x, scroll_x + width).extend_cell_length(width)
        if index == self.current:
            line = line.apply_style(CURRENT_LINE)
        return line.apply_style(self.rich_style)

    @property
    def _failure_set(self) -> set[int]:
        if getattr(self, '_failure_cache_key', None) != len(self.failures):
            self._failure_cache = set(self.failures)
            self._failure_cache_key = len(self.failures)
        return self._failure_cache

    # --- navigation -------------------------------------------------------------------------------

    def go_to(self, index: int) -> None:
        """Highlight line `index` and scroll it into view, a third of the way down."""
        if not self.lines:
            return
        self.current = max(0, min(index, len(self.lines) - 1))
        self.refresh()
        # After the refresh, so a freshly loaded log's size is known and the scroll isn't clamped.
        self.call_after_refresh(self._scroll_to_current)

    def _scroll_to_current(self) -> None:
        if self.current is not None:
            height = self.scrollable_content_region.height
            self.scroll_to(0, max(0, self.current - height // 3), animate=False)

    def _step(self, targets: list[int], step: int) -> int | None:
        if not targets:
            return None
        here = self.current if self.current is not None else int(self.scroll_offset.y)
        if step > 0:
            return next((i for i in targets if i > here), targets[0])
        return next((i for i in reversed(targets) if i < here), targets[-1])

    def action_failure(self, step: int) -> None:
        target = self._step(self.failures, step)
        if target is None:
            self.notify('No likely failures in this log')
        else:
            self.go_to(target)

    def search(self, text: str) -> int:
        """Find `text` (case-insensitive); go to the next match. Returns the match count."""
        self.search_text = text.lower()
        self.matches = (
            [i for i, line in enumerate(self.lines) if self.search_text in plain(line).lower()]
            if self.search_text
            else []
        )
        self.action_match(1)
        return len(self.matches)

    def action_match(self, step: int) -> None:
        target = self._step(self.matches, step)
        if target is None:
            if self.search_text:
                self.notify(f'No matches for {self.search_text!r}', markup=False)
        else:
            self.go_to(target)

    def action_top(self) -> None:
        self.scroll_home(animate=False)

    def action_bottom(self) -> None:
        self.scroll_end(animate=False)
