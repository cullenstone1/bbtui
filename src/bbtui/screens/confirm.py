from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no question. Dismisses with True for yes."""

    BINDINGS = [
        Binding('y', 'answer(True)', 'Yes'),
        Binding('n,escape', 'answer(False)', 'No'),
    ]

    def __init__(self, title: str, question: str, confirm_label: str = 'Yes'):
        super().__init__()
        self.title_text = title
        self.question = question
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id='confirm'):
            yield Static(Text(self.question))
            with Horizontal(id='confirm-buttons'):
                yield Button(f'{self.confirm_label} (y)', id='confirm-yes', variant='warning')
                yield Button('Cancel (n)', id='confirm-no')

    def on_mount(self) -> None:
        self.query_one('#confirm').border_title = Text(self.title_text)
        self.query_one('#confirm-no', Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == 'confirm-yes')

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)
