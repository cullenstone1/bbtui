from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, RadioButton, RadioSet, Static, TextArea

from bbtui.merge import Check
from bbtui.models import PullRequest
from bbtui.text import one_line

STRATEGY_LABELS = {
    'merge_commit': 'Merge commit',
    'squash': 'Squash',
    'fast_forward': 'Fast forward',
    'squash_fast_forward': 'Squash, fast-forward only',
    'rebase_fast_forward': 'Rebase, fast-forward',
    'rebase_merge': 'Rebase, merge',
}
WITHOUT_MESSAGE = {'fast_forward', 'rebase_fast_forward'}
"""Strategies that create no new commit, so take no message."""
CHECK_MARKS = {'ok': ('✔', 'green'), 'blocked': ('✗', 'red'), 'pending': ('●', 'yellow')}


@dataclass(frozen=True)
class MergeChoice:
    strategy: str
    message: str | None
    close_source_branch: bool


def default_merge_message(pr: PullRequest) -> str:
    """Bitbucket's default: `Merged in <branch> (pull request #N)`, the title, approvers."""
    lines = [f'Merged in {pr.source_branch} (pull request #{pr.id})', '', pr.title]
    approvers = [p.user.display_name for p in pr.participants if p.approved]
    if approvers:
        lines += ['', *(f'Approved-by: {name}' for name in approvers)]
    return '\n'.join(lines)


class MergeScreen(ModalScreen[MergeChoice | None]):
    BINDINGS = [
        Binding('ctrl+s', 'merge', 'Merge', priority=True),
        Binding('escape', 'cancel', 'Cancel', priority=True),
    ]

    def __init__(
        self,
        pr: PullRequest,
        checks: list[Check],
        strategies: list[str],
        default_strategy: str | None,
    ):
        super().__init__()
        self.pr = pr
        self.checks = checks
        self.strategies = strategies or ['merge_commit']
        self.default_strategy = (
            default_strategy if default_strategy in self.strategies else self.strategies[0]
        )

    @property
    def blocked(self) -> bool:
        return any(check.state == 'blocked' for check in self.checks)

    def compose(self) -> ComposeResult:
        checks = Text()
        for check in self.checks:
            mark, style = CHECK_MARKS[check.state]
            checks.append(f'{mark} ', style=style)
            checks.append(
                one_line(check.label) + '\n', style=style if check.state == 'blocked' else ''
            )
        checks.rstrip()
        with Vertical(id='merge-dialog'):
            yield Static(
                Text(
                    f'{one_line(self.pr.source_branch)} → {one_line(self.pr.destination_branch)}',
                    'cyan',
                )
            )
            yield Static(checks, id='merge-check-list')
            yield Label('Strategy')
            with RadioSet(id='merge-strategy'):
                for strategy in self.strategies:
                    yield RadioButton(
                        STRATEGY_LABELS.get(strategy, strategy),
                        value=strategy == self.default_strategy,
                        name=strategy,
                    )
            yield Label('Commit message', id='merge-message-label')
            yield TextArea(default_merge_message(self.pr), id='merge-message', soft_wrap=True)
            yield Checkbox(
                'Close source branch', self.pr.close_source_branch, id='merge-close-source'
            )
            with Horizontal(id='merge-buttons'):
                yield Button(
                    'Merge anyway (ctrl+s)' if self.blocked else 'Merge (ctrl+s)',
                    id='merge-confirm',
                    variant='error' if self.blocked else 'success',
                )
                yield Button('Cancel (esc)', id='merge-cancel')

    def on_mount(self) -> None:
        self.query_one('#merge-dialog').border_title = Text(
            f'Merge #{self.pr.id} {one_line(self.pr.title)}'
        )
        self.show_message_for(self.default_strategy)
        self.query_one(RadioSet).focus()

    @property
    def strategy(self) -> str:
        pressed = self.query_one(RadioSet).pressed_button
        return pressed.name if pressed and pressed.name else self.default_strategy

    def show_message_for(self, strategy: str) -> None:
        takes_message = strategy not in WITHOUT_MESSAGE
        self.query_one('#merge-message').display = takes_message
        self.query_one('#merge-message-label').display = takes_message

    @on(RadioSet.Changed)
    def strategy_changed(self) -> None:
        self.show_message_for(self.strategy)

    @on(Button.Pressed, '#merge-confirm')
    def action_merge(self) -> None:
        strategy = self.strategy
        message = None
        if strategy not in WITHOUT_MESSAGE:
            message = self.query_one('#merge-message', TextArea).text.strip() or None
        self.dismiss(
            MergeChoice(strategy, message, self.query_one('#merge-close-source', Checkbox).value)
        )

    @on(Button.Pressed, '#merge-cancel')
    def action_cancel(self) -> None:
        self.dismiss(None)
