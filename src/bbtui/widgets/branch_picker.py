import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from bbtui.models import Branch
from bbtui.text import one_line, relative

SEARCH_DELAY = 0.25


class BranchPicker(Vertical):
    """An input with a list of matching branches (most recent first), filtered server-side as
    you type. Posts `BranchPicker.Chosen` when a branch is picked."""

    BINDINGS = [Binding('down', 'focus_list', 'Branches', show=False)]

    class Chosen(Message):
        def __init__(self, picker: 'BranchPicker', branch: str):
            super().__init__()
            self.picker = picker
            self.branch = branch

        @property
        def control(self) -> 'BranchPicker':
            return self.picker

    def __init__(self, workspace: str, repo_slug: str, **kwargs):
        super().__init__(classes='branch-picker', **kwargs)
        self.workspace = workspace
        self.repo_slug = repo_slug
        self.chosen: str | None = None
        self._suppress_search = False
        self._results_for: str | None = None
        """The filter text the listed branches were fetched for."""

    def compose(self) -> ComposeResult:
        yield Input(placeholder='Type to filter branches')
        yield OptionList()

    def on_mount(self) -> None:
        self.search('')

    @property
    def value(self) -> str:
        return self.query_one(Input).value.strip()

    def choose(self, branch: str) -> None:
        """Set the branch programmatically (e.g. a default), posting `Chosen`."""
        self._suppress_search = True
        self.query_one(Input).value = branch
        self.chosen = branch
        self.post_message(self.Chosen(self, branch))

    @on(Input.Changed)
    def filter_changed(self, event: Input.Changed) -> None:
        event.stop()
        if self._suppress_search:
            self._suppress_search = False
            return
        self.chosen = None
        self.search(event.value.strip())

    @work(exclusive=True, exit_on_error=False)
    async def search(self, text: str, choose_first: bool = False) -> None:
        """List branches matching `text`; with `choose_first`, also pick the best match."""
        if not choose_first:
            await asyncio.sleep(SEARCH_DELAY if text else 0)
        options = self.query_one(OptionList)
        try:
            branches: list[Branch] = await self.app.api.branches(  # type: ignore[attr-defined]
                self.workspace, self.repo_slug, text
            )
        except Exception as exc:
            options.clear_options()
            options.add_option(
                Option(Text(f'Could not load branches: {exc}', 'red'), disabled=True)
            )
            return
        options.clear_options()
        for branch in branches:
            label = Text(one_line(branch.name))
            if branch.updated_on:
                label.append(f'  {relative(branch.updated_on)}', style='dim')
            options.add_option(Option(label, id=branch.name))
        if not branches:
            options.add_option(Option(Text('No matching branches', 'dim'), disabled=True))
        else:
            options.highlighted = 0
        self._results_for = text
        if choose_first:
            if branches:
                names = [b.name for b in branches]
                self.choose(text if text in names else names[0])
            else:
                self.notify(f'No branch matches {text!r}', severity='warning', markup=False)

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        event.stop()
        options = self.query_one(OptionList)
        exact = event.value.strip()
        if self._results_for != exact:
            # The list is still for an older filter; search now and pick from the results.
            self.search(exact, choose_first=True)
            return
        ids = [options.get_option_at_index(i).id for i in range(options.option_count)]
        if exact in ids:
            self.choose(exact)
        elif options.highlighted is not None and (option_id := ids[options.highlighted]):
            self.choose(option_id)

    @on(OptionList.OptionSelected)
    def option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id:
            self.choose(event.option.id)
            self.query_one(Input).focus()

    def action_focus_list(self) -> None:
        if self.query_one(Input).has_focus:
            self.query_one(OptionList).focus()
