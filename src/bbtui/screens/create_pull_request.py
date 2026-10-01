import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    SelectionList,
    Static,
    TextArea,
)

from bbtui.api import BitbucketError, PermissionDeniedError
from bbtui.git import current_branch_for
from bbtui.models import Commit, DiffStat, PullRequest, Repository
from bbtui.pull_request_defaults import default_description, default_title
from bbtui.screens.base import BaseScreen
from bbtui.text import one_line
from bbtui.widgets.branch_picker import BranchPicker

MAX_LISTED_COMMITS = 20
WRITE_SCOPE_HINT = 'the API token needs the write:pullrequest:bitbucket scope'


async def _optional(call):
    try:
        return await call
    except BitbucketError as exc:
        return exc


def preview_text(commits: list[Commit], diffstat: list[DiffStat] | None) -> Text:
    text = Text()
    if diffstat is not None:
        added = sum(s.lines_added for s in diffstat)
        removed = sum(s.lines_removed for s in diffstat)
        noun = 'file' if len(diffstat) == 1 else 'files'
        text.append(f'{len(diffstat)} {noun} changed  ', style='bold')
        text.append(f'+{added} ', style='green')
        text.append(f'−{removed}\n', style='red')
    for commit in commits[:MAX_LISTED_COMMITS]:
        text.append(f'{commit.hash[:7]} ', style='yellow')
        text.append(one_line(commit.summary) + '\n')
    if len(commits) > MAX_LISTED_COMMITS:
        text.append(f'… and {len(commits) - MAX_LISTED_COMMITS} more\n', style='dim')
    text.rstrip()
    return text


class CreatePullRequestScreen(BaseScreen):
    """A form for a new pull request. Dismisses with the created `PullRequest`, or None."""

    BINDINGS = [
        Binding('ctrl+s', 'create', 'Create', priority=True),
        Binding('escape', 'cancel', 'Cancel'),
    ]

    def __init__(self, repo: Repository):
        super().__init__()
        self.repo = repo
        self.auto_title = ''
        self.auto_description = ''
        self.commit_count: int | None = None
        self.confirm_cancel = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id='create-form'):
            with Horizontal(id='create-branches'):
                yield BranchPicker(self.repo.workspace, self.repo.slug, id='create-source')
                yield BranchPicker(self.repo.workspace, self.repo.slug, id='create-destination')
            yield Static(id='create-notes')
            yield Input(id='create-title', placeholder='Title')
            yield TextArea(id='create-description', soft_wrap=True, show_line_numbers=False)
            with Horizontal(id='create-details'):
                yield SelectionList[str](id='create-reviewers')
                yield Static(id='create-preview')
            with Horizontal(id='create-actions'):
                yield Checkbox('Draft', id='create-draft')
                yield Checkbox(
                    'Close source branch after merge',
                    self.settings.close_source_branch,
                    id='create-close-source',
                )
                yield Button('Create pull request', id='create-submit', variant='primary')
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f'{self.repo.full_name} · new pull request'
        titles = {
            '#create-source': 'Source',
            '#create-destination': 'Destination',
            '#create-title': 'Title',
            '#create-description': 'Description (Markdown)',
            '#create-reviewers': 'Reviewers',
            '#create-preview': 'Changes',
        }
        for selector, title in titles.items():
            self.query_one(selector).border_title = title
        self.query_one('#create-preview', Static).update(Text('Pick a source branch', 'dim'))
        self.load_defaults()

    def source(self) -> BranchPicker:
        return self.query_one('#create-source', BranchPicker)

    def destination(self) -> BranchPicker:
        return self.query_one('#create-destination', BranchPicker)

    def set_notes(self, *notes: Text) -> None:
        widget = self.query_one('#create-notes', Static)
        widget.update(Text('\n').join(notes) if notes else '')
        widget.display = bool(notes)

    @work(exclusive=True, group='defaults', exit_on_error=False)
    async def load_defaults(self) -> None:
        ws, slug = self.repo.workspace, self.repo.slug
        development, reviewers, me, local = await asyncio.gather(
            _optional(self.api.development_branch(ws, slug)),
            _optional(self.api.default_reviewers(ws, slug)),
            _optional(self.bbtui.current_user()),
            current_branch_for(ws, slug),
        )
        if isinstance(development, str):
            self.destination().choose(development)
        elif self.repo.main_branch:
            self.destination().choose(self.repo.main_branch)

        selection = self.query_one('#create-reviewers', SelectionList)
        my_id = getattr(me, 'account_id', None)
        if isinstance(reviewers, list):
            for user in reviewers:
                if user.uuid and user.account_id != my_id:
                    selection.add_option((one_line(user.display_name), user.uuid, True))
        if not selection.option_count:
            selection.add_option(('No default reviewers', '', False))
            selection.disable_messages = True

        if local and local != development:
            if await self.api.branch_exists(ws, slug, local):
                self.source().choose(local)
            else:
                self.set_notes(
                    Text(
                        f"Your checked-out branch {one_line(local)} isn't on Bitbucket yet; "
                        'push it first.',
                        style='yellow',
                    )
                )
        if not self.source().chosen:
            self.source().query_one(Input).focus()

    @on(BranchPicker.Chosen)
    def branch_chosen(self, event: BranchPicker.Chosen) -> None:
        self.refresh_preview()

    @work(exclusive=True, group='preview', exit_on_error=False)
    async def refresh_preview(self) -> None:
        source, destination = self.source().chosen, self.destination().chosen
        preview = self.query_one('#create-preview', Static)
        if not source or not destination:
            return
        if source == destination:
            self.commit_count = 0
            self.set_notes(Text('Source and destination are the same branch.', style='red'))
            preview.update('')
            return
        ws, slug = self.repo.workspace, self.repo.slug
        preview.loading = True
        try:
            commits, diffstat, existing = await asyncio.gather(
                self.api.commits_between(ws, slug, source, destination),
                _optional(self.api.diffstat_between(ws, slug, source, destination)),
                _optional(self.api.open_pull_requests_from(ws, slug, source)),
            )
        except Exception as exc:
            self.report_error(exc, 'Comparing branches')
            return
        finally:
            preview.loading = False

        self.commit_count = len(commits)
        preview.update(preview_text(commits, diffstat if isinstance(diffstat, list) else None))
        noun = 'commit' if len(commits) == 1 else 'commits'
        preview.border_title = f'Changes · {len(commits)} {noun}'
        notes = []
        if not commits:
            notes.append(
                Text(
                    f'{one_line(source)} has nothing to merge into {one_line(destination)}.', 'red'
                )
            )
        if isinstance(existing, list):
            notes.extend(
                Text(f'Already open from this branch: #{pr.id} {one_line(pr.title)}', 'yellow')
                for pr in existing
            )
        self.set_notes(*notes)
        self.fill_defaults(source, commits)

    def fill_defaults(self, source: str, commits: list[Commit]) -> None:
        """Fill the title and description, unless you've edited them."""
        title = self.query_one('#create-title', Input)
        if title.value in ('', self.auto_title):
            self.auto_title = default_title(source, commits)
            title.value = self.auto_title
        description = self.query_one('#create-description', TextArea)
        if description.text in ('', self.auto_description):
            self.auto_description = default_description(commits)
            description.load_text(self.auto_description)

    @on(Button.Pressed, '#create-submit')
    def submit_pressed(self) -> None:
        self.action_create()

    def action_create(self) -> None:
        source = self.source().chosen or self.source().value
        destination = self.destination().chosen or self.destination().value
        title = self.query_one('#create-title', Input).value.strip()
        problem = None
        if not source or not destination:
            problem = 'Pick a source and a destination branch'
        elif source == destination:
            problem = 'Source and destination are the same branch'
        elif not title:
            problem = 'Give the pull request a title'
        elif self.commit_count == 0:
            problem = 'There are no changes to merge'
        if problem:
            self.notify(problem, severity='warning')
            return
        self.create(source, destination, title)

    @work(exclusive=True, group='create', exit_on_error=False)
    async def create(self, source: str, destination: str, title: str) -> None:
        button = self.query_one('#create-submit', Button)
        button.disabled = True
        try:
            pr: PullRequest = await self.api.create_pull_request(
                self.repo.workspace,
                self.repo.slug,
                title=title,
                source=source,
                destination=destination,
                description=self.query_one('#create-description', TextArea).text,
                reviewer_uuids=[
                    uuid
                    for uuid in self.query_one('#create-reviewers', SelectionList).selected
                    if uuid
                ],
                close_source_branch=self.query_one('#create-close-source', Checkbox).value,
                draft=self.query_one('#create-draft', Checkbox).value,
            )
        except Exception as exc:
            if isinstance(exc, PermissionDeniedError):
                exc = PermissionDeniedError(f'{exc} ({WRITE_SCOPE_HINT})', exc.status_code)
            self.report_error(exc, 'Creating pull request')
            return
        finally:
            button.disabled = False
        self.notify(f'Created #{pr.id}', markup=False)
        self.dismiss(pr)

    def action_cancel(self) -> None:
        title = self.query_one('#create-title', Input).value
        description = self.query_one('#create-description', TextArea).text
        edited = title not in ('', self.auto_title) or description not in (
            '',
            self.auto_description,
        )
        if edited and not self.confirm_cancel:
            self.confirm_cancel = True
            self.notify('You have edits; press Esc again to discard them')
            return
        self.dismiss(None)
