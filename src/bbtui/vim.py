"""Vim-style h/j/k/l navigation for every screen.

Screens bind the keys and pass the movement to whatever has focus: lists and tables move their
cursor, radio sets their selection, the tab bar switches tabs, and anything else scrolls its
nearest scrollable container. Text inputs and editors handle the letters themselves first, so
typing is unaffected.
"""

import inspect

from textual.binding import Binding
from textual.widget import Widget

VIM_BINDINGS = [
    Binding('h', "vim('left')", 'Left', show=False),
    Binding('j', "vim('down')", 'Down', show=False),
    Binding('k', "vim('up')", 'Up', show=False),
    Binding('l', "vim('right')", 'Right', show=False),
]

WIDGET_ACTIONS = {
    'down': ('cursor_down', 'next_button'),
    'up': ('cursor_up', 'previous_button'),
    'left': ('previous_tab',),
    'right': ('next_tab',),
}
"""Actions tried on the focused widget itself, in order."""
SCROLL_ACTIONS = {
    'down': 'scroll_down',
    'up': 'scroll_up',
    'left': 'scroll_left',
    'right': 'scroll_right',
}


def _can_scroll(widget: Widget, direction: str) -> bool:
    if direction in ('up', 'down'):
        return widget.allow_vertical_scroll
    return widget.allow_horizontal_scroll


async def vim_move(focused: Widget | None, direction: str) -> bool:
    """Move the focused widget (or scroll its nearest scrollable ancestor). Returns whether
    anything handled it."""
    if focused is None:
        return False
    for name in WIDGET_ACTIONS[direction]:
        if action := getattr(focused, f'action_{name}', None):
            result = action()
            if inspect.isawaitable(result):
                await result
            return True
    node: Widget | None = focused
    while isinstance(node, Widget):
        if _can_scroll(node, direction):
            getattr(node, f'action_{SCROLL_ACTIONS[direction]}')()
            return True
        node = node.parent if isinstance(node.parent, Widget) else None
    return False


class VimNavigation:
    """Mixin for screens: add `*VIM_BINDINGS` to the screen's BINDINGS."""

    async def action_vim(self, direction: str) -> None:
        await vim_move(self.focused, direction)  # type: ignore[attr-defined]
