from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from bbtui.clipboard import copy_with_tool
from bbtui.models import Repository


def copy_text(screen: ModalScreen, text: str, what: str) -> None:
    """Copy `text`, saying what was copied (e.g. "the URL"), then close `screen`."""
    if copy_with_tool(text):
        screen.notify(f'Copied {what} to the clipboard')
    else:
        # No clipboard tool: ask the terminal (OSC 52). tmux passes this on only with
        # `set-clipboard on`, so it may not arrive.
        screen.app.copy_to_clipboard(text)
        screen.notify(f'Sent {what} to the terminal clipboard')
    screen.dismiss()


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
        copy_text(self, self.url, 'the URL')

    def action_open(self) -> None:
        self.app.open_url(self.url)
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


class CloneScreen(ModalScreen[None]):
    """A repository's HTTPS and SSH clone links, each with a copy shortcut."""

    BINDINGS = [
        Binding('h', 'copy("https")', 'Copy HTTPS'),
        Binding('s', 'copy("ssh")', 'Copy SSH'),
        Binding('escape,q,c', 'close', 'Close'),
    ]

    def __init__(self, repo: Repository):
        super().__init__()
        self.repo = repo
        self.links = {'https': repo.clone_https, 'ssh': repo.clone_ssh}

    def compose(self) -> ComposeResult:
        with Vertical(id='url-dialog'):
            with Vertical(id='clone-links'):
                for name, label in (('https', 'HTTPS'), ('ssh', 'SSH  ')):
                    line = Text(f'{label}  ', style='dim')
                    line.append(self.links[name], style='bold underline')
                    yield Static(line)
            with Horizontal(id='url-buttons'):
                yield Button('Copy HTTPS (h)', id='clone-https', variant='primary')
                yield Button('Copy SSH (s)', id='clone-ssh')
                yield Button('Close (esc)', id='clone-close')

    def on_mount(self) -> None:
        self.query_one('#url-dialog').border_title = Text(f'Clone {self.repo.full_name}')
        self.query_one('#clone-https', Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ('clone-https', 'clone-ssh'):
            self.action_copy(event.button.id.removeprefix('clone-'))
        else:
            self.action_close()

    def action_copy(self, name: str) -> None:
        copy_text(self, self.links[name], f'the {name.upper()} clone link')

    def action_close(self) -> None:
        self.dismiss()
