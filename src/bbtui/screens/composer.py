from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea


@dataclass(frozen=True)
class CommentTarget:
    """Where a new comment goes: the pull request, a diff line, or a reply to a comment."""

    label: str
    quote: str = ''
    """Context shown above the editor: the diff line, or the comment being replied to."""
    path: str | None = None
    line_to: int | None = None
    line_from: int | None = None
    parent_id: int | None = None

    @property
    def key(self) -> tuple:
        """Identifies the target, for keeping drafts."""
        return (self.path, self.line_to, self.line_from, self.parent_id)


class CommentComposer(ModalScreen[tuple[str, str]]):
    """A Markdown editor for one comment. Dismisses with `('post', text)` or `('cancel', text)`."""

    BINDINGS = [
        Binding('ctrl+s', 'post', 'Post', priority=True),
        Binding('escape', 'cancel', 'Cancel (keeps draft)', priority=True),
    ]

    def __init__(self, target: CommentTarget, draft: str = ''):
        super().__init__()
        self.target = target
        self.draft = draft

    def compose(self) -> ComposeResult:
        with Vertical(id='composer'):
            if self.target.quote:
                yield Static(Text(self.target.quote, style='dim'), id='composer-quote')
            yield TextArea(
                self.draft,
                id='composer-text',
                soft_wrap=True,
                show_line_numbers=False,
                tab_behavior='indent',
            )
            yield Static(
                Text('Markdown · ctrl+s post · esc cancel (draft kept)', style='dim'),
                id='composer-hint',
            )

    def on_mount(self) -> None:
        self.query_one('#composer').border_title = Text(self.target.label)
        editor = self.query_one(TextArea)
        editor.focus()
        editor.move_cursor(editor.document.end)

    def action_post(self) -> None:
        text = self.query_one(TextArea).text
        if not text.strip():
            self.notify('Write something first', severity='warning')
            return
        self.dismiss(('post', text))

    def action_cancel(self) -> None:
        self.dismiss(('cancel', self.query_one(TextArea).text))
