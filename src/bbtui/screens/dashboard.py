from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView

from bbtui.api.api import split_repo_name
from bbtui.models import Repository
from bbtui.screens.base import BaseScreen
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
        with Vertical(id='dashboard'):
            yield ListView(id='starred')
            yield ListView(id='others')
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.settings.workspace or ''
        self.query_one('#search', Input).border_title = 'Search'
        self.query_one('#starred', ListView).border_title = 'Starred'
        self.query_one('#others', ListView).border_title = 'Recently updated'
        self.load_dashboard()

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
        self.load_others(self.query_one('#search', Input).value.strip())

    @on(Input.Submitted, '#search')
    def search(self, event: Input.Submitted) -> None:
        self.load_others(event.value.strip())
        self.query_one('#others', ListView).focus()

    @on(ListView.Selected)
    def open_repository(self, event: ListView.Selected) -> None:
        if isinstance(event.item, RepositoryItem):
            self.app.push_screen(PullRequestsScreen(event.item.repo))

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
