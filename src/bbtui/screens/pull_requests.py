from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header

from bbtui.models import PullRequest, Repository
from bbtui.screens.base import BaseScreen
from bbtui.screens.commits import CommitsScreen
from bbtui.screens.create_pull_request import CreatePullRequestScreen
from bbtui.screens.pipelines import PipelinesScreen
from bbtui.screens.pull_request_detail import PullRequestDetailScreen
from bbtui.screens.url import CloneScreen, UrlScreen
from bbtui.text import ago, one_line, truncate

STATES = ('OPEN', 'MERGED', 'DECLINED')


def review_text(pr: PullRequest) -> Text:
    text = Text()
    if pr.approvals:
        text.append(f'✔{pr.approvals}', style='green')
    if pr.changes_requested:
        text.append(f' ✗{pr.changes_requested}', style='red')
    if not text:
        text.append(f'·{len(pr.reviewers)}', style='dim')
    return text


class PullRequestsScreen(BaseScreen):
    BINDINGS = [
        Binding('escape', 'app.pop_screen', 'Back'),
        Binding('r', 'refresh', 'Refresh'),
        Binding('s', 'cycle_state', 'State'),
        Binding('u', 'show_url', 'URL'),
        Binding('c', 'clone', 'Clone'),
        Binding('n', 'new_pull_request', 'New PR'),
        Binding('P', 'pipelines', 'Pipelines'),
        Binding('C', 'commits', 'Commits'),
    ]

    def __init__(self, repo: Repository):
        super().__init__()
        self.repo = repo
        self.state = STATES[0]
        self.pull_requests: dict[str, PullRequest] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id='pull-requests', cursor_type='row', zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.border_title = Text(f'Pull requests · {one_line(self.repo.full_name)}')
        table.add_column('#', key='id')
        table.add_column('Title', key='title')
        table.add_column('Author', key='author')
        table.add_column('Branch', key='branch')
        table.add_column('Reviews', key='reviews')
        table.add_column('💬', key='comments')
        table.add_column('Updated', key='updated')
        self.load_pull_requests()

    @work(exclusive=True, exit_on_error=False)
    async def load_pull_requests(self) -> None:
        self.sub_title = f'{self.repo.full_name} · {self.state.lower()} pull requests'
        table = self.query_one(DataTable)
        table.loading = True
        try:
            pull_requests = await self.api.pull_requests(
                self.repo.workspace, self.repo.slug, self.state
            )
        except Exception as exc:
            self.report_error(exc, 'Loading pull requests')
            return
        finally:
            table.loading = False
        table.clear()
        table.border_subtitle = f'{self.state.lower()} · {len(pull_requests)}'
        self.pull_requests = {str(pr.id): pr for pr in pull_requests}
        for pr in pull_requests:
            table.add_row(
                Text(str(pr.id), style='bold'),
                Text(truncate(('[draft] ' if pr.draft else '') + one_line(pr.title), 70)),
                Text(truncate(one_line(pr.author.display_name), 20)),
                Text(
                    f'{truncate(one_line(pr.source_branch), 30)} → '
                    f'{truncate(one_line(pr.destination_branch), 20)}',
                    'dim',
                ),
                review_text(pr),
                Text(str(pr.comment_count)),
                Text(ago(pr.updated_on)),
                key=str(pr.id),
            )
        if not pull_requests:
            self.notify(f'No {self.state.lower()} pull requests', markup=False)

    @on(DataTable.RowSelected)
    def open_pull_request(self, event: DataTable.RowSelected) -> None:
        pr = self.pull_requests.get(str(event.row_key.value))
        if pr:
            self.app.push_screen(
                PullRequestDetailScreen(self.repo.workspace, self.repo.slug, pr.id)
            )

    def selected_pull_request(self) -> PullRequest | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return self.pull_requests.get(str(row_key.value))

    def action_refresh(self) -> None:
        self.load_pull_requests()

    def action_cycle_state(self) -> None:
        self.state = STATES[(STATES.index(self.state) + 1) % len(STATES)]
        self.load_pull_requests()

    def action_show_url(self) -> None:
        pr = self.selected_pull_request()
        if pr:
            self.app.push_screen(UrlScreen(f'#{pr.id} {one_line(pr.title)}', pr.url))
        elif self.repo.html_url:
            self.app.push_screen(UrlScreen(one_line(self.repo.full_name), self.repo.html_url))

    def action_clone(self) -> None:
        self.app.push_screen(CloneScreen(self.repo))

    def on_screen_resume(self) -> None:
        # Coming back from a pull request: it may have been merged, approved or marked ready.
        if self.pull_requests:
            self.load_pull_requests()

    def action_new_pull_request(self) -> None:
        def created(pr: PullRequest | None) -> None:
            if pr is None:
                return
            if self.state != 'OPEN':
                self.state = 'OPEN'
            self.load_pull_requests()
            self.app.push_screen(
                PullRequestDetailScreen(self.repo.workspace, self.repo.slug, pr.id)
            )

        self.app.push_screen(CreatePullRequestScreen(self.repo), created)

    def action_pipelines(self) -> None:
        self.app.push_screen(PipelinesScreen(self.repo))

    def action_commits(self) -> None:
        self.app.push_screen(CommitsScreen(self.repo))
