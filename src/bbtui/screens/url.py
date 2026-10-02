from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from bbtui.clipboard import copy_with_tool


class UrlScreen(ModalScreen[None]):
    """Shows a URL so it can be read or selected, with copy and open shortcuts."""

    BINDINGS = [
        Binding('y', 'copy', 'Copy'),
        Binding('o', 'open', 'Open in browser'),
        Binding('escape,q,u', 'close', 'Close'),
    ]

    def __init__(self, title: str, url: str):
        super().__init__()
        self.title_text = title
        self.url = url

    def compose(self) -> ComposeResult:
        with Vertical(id='url-dialog'):
            yield Static(Text(self.url, style='bold underline'), id='url-text')
            with Horizontal(id='url-buttons'):
                yield Button('Copy (y)', id='url-copy', variant='primary')
                yield Button('Open (o)', id='url-open')
                yield Button('Close (esc)', id='url-close')

    def on_mount(self) -> None:
        self.query_one('#url-dialog').border_title = Text(self.title_text)
        self.query_one('#url-copy', Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {'url-copy': self.action_copy, 'url-open': self.action_open}.get(
            event.button.id or '', self.action_close
        )()

    def action_copy(self) -> None:
        if copy_with_tool(self.url):
            self.notify('Copied the URL to the clipboard')
        else:
            # No clipboard tool: ask the terminal (OSC 52). tmux passes this on only with
            # `set-clipboard on`, so it may not arrive.
            self.app.copy_to_clipboard(self.url)
            self.notify('Sent the URL to the terminal clipboard')
        self.dismiss()

    def action_open(self) -> None:
        self.app.open_url(self.url)
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()
