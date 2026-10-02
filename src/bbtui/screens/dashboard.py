import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView

from bbtui.api.api import split_repo_name
from bbtui.merge import build_check
from bbtui.models import BuildStatus, Pipeline, PullRequest, Repository, Schedule
from bbtui.screens.base import BaseScreen
from bbtui.screens.pipeline_run import PipelineRunScreen, run_url, status_text
from bbtui.screens.pull_request_detail import PullRequestDetailScreen
from bbtui.screens.pull_requests import PullRequestsScreen
from bbtui.screens.url import UrlScreen
from bbtui.text import duration, one_line, relative
from bbtui.watch import WatchedBuild


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


class NightlyItem(ListItem):
    """The latest run of a scheduled pipeline: what it is, then how it went."""

    def __init__(self, repo: Repository, schedule: Schedule, run: Pipeline | None):
        super().__init__()
        self.repo = repo
        self.schedule = schedule
        self.run = run

    def compose(self) -> ComposeResult:
        title = Text(one_line(self.repo.slug), style='bold')
        title.append(f'  {one_line(self.schedule.name)}', style='cyan')
        if self.schedule.ref_name and self.schedule.ref_name != self.schedule.name:
            title.append(f' on {one_line(self.schedule.ref_name)}', style='dim')
        if not self.schedule.enabled:
            title.append('  (disabled)', style='dim')
        if self.run is None:
            status = Text('no recent scheduled run', style='dim')
        else:
            status = status_text(self.run.status)
            status.append(f'  #{self.run.build_number}', style='bold')
            status.append(f' · {relative(self.run.created_on)}', style='dim')
            if self.run.duration_seconds:
                status.append(f' · took {duration(self.run.duration_seconds)}', style='dim')
        with Vertical(classes='pr-item'):
            yield Label(title, classes='pr-title')
            yield Label(status, classes='pr-meta')


class DashboardScreen(BaseScreen):
    AUTO_FOCUS = '#starred'
    BINDINGS = [
        Binding('slash', 'focus_search', 'Search'),
        Binding('r', 'refresh', 'Refresh'),
        Binding('u', 'show_url', 'URL'),
        Binding('escape', 'clear_search', 'Clear search', show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder='Search repositories by name, then Enter', id='search')
        with Horizontal(id='dashboard'):
            with Vertical(id='repo-column'):
                yield ListView(id='starred')
                yield ListView(id='others')
            with Vertical(id='right-column'):
                yield ListView(id='nightly')
                yield ListView(id='mine')
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.settings.workspace or ''
        self.query_one('#search', Input).border_title = 'Search'
        self.query_one('#starred', ListView).border_title = 'Starred'
        self.query_one('#others', ListView).border_title = 'Recently updated'
        self.query_one('#mine', ListView).border_title = 'My pull requests'
        self.last_focused: dict[tuple[str, ...], ListView] = {}
        self.query_one('#nightly', ListView).border_title = 'Scheduled pipelines'
        self.query_one('#nightly', ListView).display = False
        self.load_dashboard()

    def on_screen_resume(self) -> None:
        # Coming back from a pull request or a run: reviews or builds may have changed.
        if self.query_one('#mine', ListView).children:
            self.load_mine()
        if self.query_one('#nightly', ListView).children:
            self.load_nightly()

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
        for pr, result in zip(pull_requests, statuses, strict=True):
            if not isinstance(result, list):
                continue
            pr_workspace, pr_slug = pr.repository.split('/', 1)
            for status in result:
                if status.state == 'INPROGRESS' and status.url:
                    self.bbtui.watch_build(
                        status.url,
                        WatchedBuild(
                            pr_workspace, pr_slug, pr.id, status.key, f'#{pr.id} {pr.title}'
                        ),
                    )

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
        self.load_nightly()

    @work(exclusive=True, group='nightly', exit_on_error=False)
    async def load_nightly(self) -> None:
        """Latest runs of the scheduled pipelines (e.g. nightlies) in your starred repositories."""
        nightly = self.query_one('#nightly', ListView)
        workspace = self.settings.workspace or ''
        try:
            repos, _ = await self.api.repositories(self.settings.starred_repos, workspace)
            found = await asyncio.gather(
                *(self.api.schedules(repo.workspace, repo.slug) for repo in repos),
                return_exceptions=True,
            )
            pairs = [
                (repo, schedule)
                for repo, schedules in zip(repos, found, strict=True)
                if isinstance(schedules, list)
                for schedule in schedules
            ]
            runs = await asyncio.gather(
                *(
                    self.api.latest_scheduled_run(repo.workspace, repo.slug, schedule)
                    for repo, schedule in pairs
                ),
                return_exceptions=True,
            )
        except Exception as exc:
            self.report_error(exc, 'Loading scheduled pipelines')
            return
        index = nightly.index
        await nightly.clear()
        nightly.display = bool(pairs)
        if not pairs:
            return
        await nightly.extend(
            NightlyItem(repo, schedule, run if isinstance(run, Pipeline) else None)
            for (repo, schedule), run in zip(pairs, runs, strict=True)
        )
        nightly.index = min(index or 0, len(pairs) - 1)
        failed = sum(
            1 for run in runs if isinstance(run, Pipeline) and run.status in ('FAILED', 'ERROR')
        )
        nightly.border_subtitle = Text(f'{failed} failed', 'red') if failed else str(len(pairs))
        self.load_others(self.query_one('#search', Input).value.strip())

    @on(Input.Submitted, '#search')
    def search(self, event: Input.Submitted) -> None:
        self.load_others(event.value.strip())
        self.query_one('#others', ListView).focus()

    @on(ListView.Selected)
    def open_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, RepositoryItem):
            self.app.push_screen(PullRequestsScreen(event.item.repo))
        elif isinstance(event.item, NightlyItem) and event.item.run:
            repo, run = event.item.repo, event.item.run
            self.app.push_screen(PipelineRunScreen(repo.workspace, repo.slug, run.build_number))
        elif isinstance(event.item, PullRequestItem):
            workspace, slug = event.item.pr.repository.split('/', 1)
            self.app.push_screen(PullRequestDetailScreen(workspace, slug, event.item.pr.id))

    LEFT_LISTS = ('starred', 'others')
    RIGHT_LISTS = ('nightly', 'mine')

    async def action_vim(self, direction: str) -> None:
        focused = self.focused
        if direction in ('left', 'right') and isinstance(focused, ListView) and focused.id:
            if focused.id in self.LEFT_LISTS and direction == 'right':
                self.focus_column(self.RIGHT_LISTS)
                return
            if focused.id in self.RIGHT_LISTS and direction == 'left':
                self.focus_column(self.LEFT_LISTS)
                return
        await super().action_vim(direction)

    def focus_column(self, ids: tuple[str, ...]) -> None:
        """Focus the list in that column you were last in, else its first visible one."""
        lists = [self.query_one(f'#{list_id}', ListView) for list_id in ids]
        visible = [view for view in lists if view.display]
        last = self.last_focused.get(ids)
        target = last if last in visible else (visible[0] if visible else None)
        if target:
            target.focus()

    def on_descendant_focus(self, event) -> None:
        widget = event.widget
        for column in (self.LEFT_LISTS, self.RIGHT_LISTS):
            if isinstance(widget, ListView) and widget.id in column:
                self.last_focused[column] = widget

    def action_show_url(self) -> None:
        """The URL of the highlighted pull request, scheduled run or repository."""
        focused = self.focused
        item = focused.highlighted_child if isinstance(focused, ListView) else None
        if isinstance(item, PullRequestItem):
            self.app.push_screen(UrlScreen(f'#{item.pr.id} {one_line(item.pr.title)}', item.pr.url))
        elif isinstance(item, NightlyItem) and item.run:
            url = run_url(item.repo.workspace, item.repo.slug, item.run.build_number)
            self.app.push_screen(UrlScreen(f'{item.repo.slug} #{item.run.build_number}', url))
        elif isinstance(item, RepositoryItem) and item.repo.html_url:
            self.app.push_screen(UrlScreen(one_line(item.repo.full_name), item.repo.html_url))

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
