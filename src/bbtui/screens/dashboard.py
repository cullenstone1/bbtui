import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView

from bbtui.api.api import split_repo_name
from bbtui.merge import build_check
from bbtui.models import BuildStatus, PullRequest, Repository
from bbtui.screens.base import BaseScreen
from bbtui.screens.pull_request_detail import PullRequestDetailScreen
from bbtui.screens.pull_requests import PullRequestsScreen
from bbtui.text import one_line, relative


class RepositoryItem(ListItem):
    """A repository as one row: name, description, and when it was last updated."""

    def __init__(self, repo: Repository, current_workspace: str):
        super().__init__()
        self.repo = repo
        self.current_workspace = current_workspace

    def compose(self) -> ComposeResult:
        repo = self.repo
        name = repo.slug if repo.workspace == self.current_workspace else repo.full_name
        with Horizontal(classes='repo-row'):
            yield Label(Text(one_line(name)), classes='repo-name')
            yield Label(Text(one_line(repo.description)), classes='repo-description')
            yield Label(Text(relative(repo.updated_on)), classes='repo-updated')


def build_mark(statuses: list[BuildStatus] | None) -> Text:
    if not statuses:
        return Text()
    check = build_check(statuses)
    mark, style = {'ok': ('✔', 'green'), 'blocked': ('✗', 'red'), 'pending': ('●', 'yellow')}[
        check.state
    ]
    label = {'ok': 'build', 'blocked': 'build', 'pending': 'building'}[check.state]
    return Text(f'{mark} {label}', style=style)


def pull_request_meta(
    pr: PullRequest, statuses: list[BuildStatus] | None, current_workspace: str
) -> Text:
    """`repo · ✔ 1/7 ✗1 · 💬 3 · ✔ build · 2 hours ago` (approvals out of reviewers)."""
    workspace, _, slug = pr.repository.partition('/')
    parts: list[Text] = [Text(one_line(slug if workspace == current_workspace else pr.repository))]
    reviews = Text(
        f'✔ {pr.approvals}/{len(pr.reviewers)}', style='green' if pr.approvals else 'dim'
    )
    if pr.changes_requested:
        reviews.append(f' ✗{pr.changes_requested}', style='red')
    parts.append(reviews)
    if pr.comment_count:
        parts.append(Text(f'💬 {pr.comment_count}'))
    if builds := build_mark(statuses):
        parts.append(builds)
    parts.append(Text(relative(pr.updated_on)))
    return Text(' · ', style='dim').join(parts)


class PullRequestItem(ListItem):
    """One of your pull requests: title on the first line, status on the second."""

    def __init__(self, pr: PullRequest, statuses: list[BuildStatus] | None, current_workspace: str):
        super().__init__()
        self.pr = pr
        self.statuses = statuses
        self.current_workspace = current_workspace

    def compose(self) -> ComposeResult:
        title = Text(f'#{self.pr.id} ', style='bold')
        if self.pr.draft:
            title.append('[draft] ', style='yellow')
        title.append(one_line(self.pr.title))
        with Vertical(classes='pr-item'):
            yield Label(title, classes='pr-title')
            yield Label(
                pull_request_meta(self.pr, self.statuses, self.current_workspace),
                classes='pr-meta',
            )


class DashboardScreen(BaseScreen):
    AUTO_FOCUS = '#starred'
    BINDINGS = [
        Binding('slash', 'focus_search', 'Search'),
        Binding('r', 'refresh', 'Refresh'),
        Binding('escape', 'clear_search', 'Clear search', show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder='Search repositories by name, then Enter', id='search')
        with Horizontal(id='dashboard'):
            with Vertical(id='repo-column'):
                yield ListView(id='starred')
                yield ListView(id='others')
            yield ListView(id='mine')
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.settings.workspace or ''
        self.query_one('#search', Input).border_title = 'Search'
        self.query_one('#starred', ListView).border_title = 'Starred'
        self.query_one('#others', ListView).border_title = 'Recently updated'
        self.query_one('#mine', ListView).border_title = 'My pull requests'
        self.load_dashboard()

    def on_screen_resume(self) -> None:
        # Coming back from a pull request: its reviews or builds may have changed.
        if self.query_one('#mine', ListView).children:
            self.load_mine()

    @work(exclusive=True, group='mine', exit_on_error=False)
    async def load_mine(self) -> None:
        mine = self.query_one('#mine', ListView)
        workspace = self.settings.workspace or ''
        mine.loading = not mine.children
        try:
            me = await self.bbtui.current_user()
            pull_requests = await self.api.pull_requests_by(workspace, me.uuid or '')
            # Build status per pull request; a failed lookup just leaves it out.
            statuses = await asyncio.gather(
                *(
                    self.api.pull_request_statuses(*pr.repository.split('/', 1), pr.id)
                    for pr in pull_requests
                ),
                return_exceptions=True,
            )
        except Exception as exc:
            self.report_error(exc, 'Loading your pull requests')
            return
        finally:
            mine.loading = False
        index = mine.index
        await mine.clear()
        mine.border_subtitle = str(len(pull_requests))
        if not pull_requests:
            await mine.append(ListItem(Label(Text('No open pull requests', 'dim'))))
            return
        await mine.extend(
            PullRequestItem(pr, result if isinstance(result, list) else None, workspace)
            for pr, result in zip(pull_requests, statuses, strict=True)
        )
        mine.index = min(index or 0, len(pull_requests) - 1)

    @work(exclusive=True, group='starred', exit_on_error=False)
    async def load_starred(self) -> None:
        starred = self.query_one('#starred', ListView)
        names = self.settings.starred_repos
        if not names:
            await starred.clear()
            await starred.append(ListItem(Label(Text('Add starred_repos to your config', 'dim'))))
            return
        starred.loading = True
        try:
            repos, missing = await self.api.repositories(names, self.settings.workspace or '')
        except Exception as exc:
            self.report_error(exc, 'Loading starred repositories')
            return
        finally:
            starred.loading = False
        await starred.clear()
        await starred.extend(RepositoryItem(repo, self.settings.workspace or '') for repo in repos)
        starred.index = 0
        starred.border_subtitle = str(len(repos))
        if missing:
            self.notify(
                f'Starred repositories not found: {", ".join(missing)}',
                severity='warning',
                markup=False,
            )

    @work(exclusive=True, group='others', exit_on_error=False)
    async def load_others(self, search: str = '') -> None:
        others = self.query_one('#others', ListView)
        workspace = self.settings.workspace or ''
        others.loading = True
        try:
            if search:
                others.border_title = Text(f'Search: {one_line(search)}')
                repos = await self.api.search_repositories(workspace, search)
            else:
                others.border_title = 'Recently updated'
                repos = await self.api.recent_repositories(
                    workspace, self.settings.recent_repos_limit
                )
        except Exception as exc:
            self.report_error(exc, 'Loading repositories')
            return
        finally:
            others.loading = False
        if not search:
            starred = {
                '/'.join(split_repo_name(name, workspace)) for name in self.settings.starred_repos
            }
            repos = [repo for repo in repos if repo.full_name not in starred]
        await others.clear()
        others.border_subtitle = str(len(repos))
        if repos:
            await others.extend(
                RepositoryItem(repo, self.settings.workspace or '') for repo in repos
            )
            others.index = 0
        else:
            await others.append(ListItem(Label(Text('No repositories found', 'dim'))))

    def load_dashboard(self) -> None:
        self.load_starred()
        self.load_mine()
        self.load_others(self.query_one('#search', Input).value.strip())

    @on(Input.Submitted, '#search')
    def search(self, event: Input.Submitted) -> None:
        self.load_others(event.value.strip())
        self.query_one('#others', ListView).focus()

    @on(ListView.Selected)
    def open_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, RepositoryItem):
            self.app.push_screen(PullRequestsScreen(event.item.repo))
        elif isinstance(event.item, PullRequestItem):
            workspace, slug = event.item.pr.repository.split('/', 1)
            self.app.push_screen(PullRequestDetailScreen(workspace, slug, event.item.pr.id))

    def action_focus_search(self) -> None:
        self.query_one('#search', Input).focus()

    def action_clear_search(self) -> None:
        search = self.query_one('#search', Input)
        if search.value:
            search.value = ''
            self.load_others()
        self.query_one('#starred', ListView).focus()

    def action_refresh(self) -> None:
        self.load_dashboard()
