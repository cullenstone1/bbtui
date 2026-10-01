import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
)

from bbtui.api import BitbucketError
from bbtui.diff import FileDiff, parse_diff
from bbtui.merge import Check, merge_checks, verdict
from bbtui.models import BuildStatus, Comment, DiffStat, PullRequest
from bbtui.screens.base import BaseScreen
from bbtui.text import ago, clean, one_line, timestamp
from bbtui.widgets import CommentView, DiffView, comment_threads, resolve_mentions
from bbtui.widgets.comments import Thread

REVIEW_MARKS = {
    'approved': ('✔', 'green'),
    'changes_requested': ('✗', 'red'),
}
STATUS_STYLES = {'A': 'green', 'D': 'red', 'R': 'yellow', 'M': 'blue'}
CHECK_MARKS = {'ok': ('✔', 'green'), 'blocked': ('✗', 'red'), 'pending': ('●', 'yellow')}
BUILD_MARKS = {
    'SUCCESSFUL': ('✔', 'green'),
    'FAILED': ('✗', 'red'),
    'STOPPED': ('■', 'red'),
    'INPROGRESS': ('●', 'yellow'),
}


async def _optional(call):
    """Await `call`, returning a Bitbucket error instead of raising it."""
    try:
        return await call
    except BitbucketError as exc:
        return exc


def known_names(pr: PullRequest, comments: list[Comment]) -> dict[str, str]:
    """Account id → display name, for everyone who appears on the pull request."""
    users = [pr.author, *(p.user for p in pr.participants), *(c.author for c in comments)]
    return {u.account_id: one_line(u.display_name) for u in users if u.account_id}


def summary_text(pr: PullRequest) -> Text:
    text = Text()
    text.append(one_line(pr.title) + '\n', style='bold')
    state_style = {'OPEN': 'green', 'MERGED': 'magenta', 'DECLINED': 'red'}.get(pr.state, '')
    text.append(f'{pr.state}{" (draft)" if pr.draft else ""}', style=f'bold {state_style}')
    text.append(f'  {one_line(pr.author.display_name)}  ')
    text.append(f'{one_line(pr.source_branch)} → {one_line(pr.destination_branch)}\n', 'cyan')
    text.append(
        f'Created {timestamp(pr.created_on)} · Updated {timestamp(pr.updated_on)} · '
        f'{pr.comment_count} comments · {pr.task_count} tasks',
        style='dim',
    )
    return text


def checks_text(checks: list[Check]) -> Text:
    text = Text()
    for check in checks:
        mark, style = CHECK_MARKS[check.state]
        text.append(f'{mark} ', style=style)
        text.append(one_line(check.label) + '\n')
    text.rstrip()
    return text


def builds_text(statuses: list[BuildStatus] | None, error: str | None = None) -> Text:
    if statuses is None:
        return Text(f'Unavailable: {one_line(error)}', style='dim')
    if not statuses:
        return Text('No builds for this pull request', style='dim')
    text = Text()
    for status in statuses:
        mark, style = BUILD_MARKS.get(status.state, ('·', 'dim'))
        text.append(f'{mark} ', style=style)
        text.append(one_line(status.name))
        if status.url and '/pipelines/results/' in status.url:
            text.append(f' #{status.url.rstrip("/").rsplit("/", 1)[-1]}', style='dim')
        text.append(f'  {status.state.lower()}', style=style)
        if status.updated_on:
            text.append(f'  {ago(status.updated_on)} ago', style='dim')
        text.append('\n')
    text.rstrip()
    return text


def reviewers_text(pr: PullRequest) -> Text:
    text = Text()
    if not pr.reviewers:
        text.append('none', style='dim')
    for reviewer in pr.reviewers:
        mark, style = REVIEW_MARKS.get(reviewer.state or '', ('·', 'dim'))
        text.append(f'{mark} ', style=style)
        text.append(one_line(reviewer.user.display_name) + '\n')
    others = [p for p in pr.participants if p.role != 'REVIEWER' and p.approved]
    if others:
        names = ', '.join(one_line(p.user.display_name) for p in others)
        text.append(f'also approved by {names}', style='green')
    text.rstrip()
    return text


class PullRequestDetailScreen(BaseScreen):
    BINDINGS = [
        Binding('escape', 'app.pop_screen', 'Back'),
        Binding('r', 'refresh', 'Refresh'),
        Binding('o', 'open_in_browser', 'Open in browser'),
        Binding('p', 'open_build', 'Open build'),
        Binding('1', "show_tab('overview')", 'Overview', show=False),
        Binding('2', "show_tab('diff')", 'Diff', show=False),
        Binding('left_square_bracket', 'step_file(-1)', 'Prev file'),
        Binding('right_square_bracket', 'step_file(1)', 'Next file'),
    ]

    def __init__(self, workspace: str, repo_slug: str, pr_id: int):
        super().__init__()
        self.workspace = workspace
        self.repo_slug = repo_slug
        self.pr_id = pr_id
        self.pull_request: PullRequest | None = None
        self.diffstat: list[DiffStat] = []
        self.file_diffs: dict[str, FileDiff] = {}
        self.inline_threads: list[Thread] = []
        self.names: dict[str, str] = {}
        self.statuses: list[BuildStatus] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id='tabs'):
            with TabPane('Overview', id='overview'):
                with VerticalScroll(id='overview-scroll'):
                    yield Static(id='summary')
                    with Horizontal(id='checks-row'):
                        yield Static(id='merge-checks')
                        yield Static(id='builds')
                    yield Static(id='reviewers')
                    yield Markdown(id='description', open_links=True)
                    yield Vertical(id='general-comments')
            with TabPane('Diff', id='diff'):
                yield DataTable(id='file-chooser', cursor_type='row', zebra_stripes=True)
                yield DiffView(id='diff-view')
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f'{self.workspace}/{self.repo_slug} · #{self.pr_id}'
        self.query_one('#summary').border_title = f'#{self.pr_id}'
        self.query_one('#merge-checks').border_title = 'Merge checks'
        self.query_one('#builds').border_title = 'Builds'
        self.query_one('#reviewers').border_title = 'Reviewers'
        self.query_one('#description').border_title = 'Description'
        self.query_one('#general-comments').border_title = 'Comments'
        self.query_one('#diff-view').border_title = 'Diff'
        chooser = self.query_one('#file-chooser', DataTable)
        chooser.border_title = 'Files'
        chooser.add_column('', key='status')
        chooser.add_column('Path', key='path')
        chooser.add_column('+', key='added')
        chooser.add_column('−', key='removed')
        chooser.add_column('💬', key='comments')
        self.load_pull_request()

    @work(exclusive=True, group='detail', exit_on_error=False)
    async def load_pull_request(self) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.loading = True
        ws, slug, pr_id = self.workspace, self.repo_slug, self.pr_id
        try:
            # Builds and tasks are nice-to-haves: a failure there shouldn't hide the PR.
            pr, diffstat, comments, diff, statuses, open_tasks = await asyncio.gather(
                self.api.pull_request(ws, slug, pr_id),
                self.api.pull_request_diffstat(ws, slug, pr_id),
                self.api.pull_request_comments(ws, slug, pr_id),
                self.api.pull_request_diff(ws, slug, pr_id),
                _optional(self.api.pull_request_statuses(ws, slug, pr_id)),
                _optional(self.api.pull_request_open_task_count(ws, slug, pr_id)),
            )
        except Exception as exc:
            self.report_error(exc, 'Loading pull request')
            return
        finally:
            tabs.loading = False
        self.show_checks(pr, diffstat, statuses, open_tasks)
        self.pull_request = pr
        self.names = known_names(pr, comments)
        self.query_one('#summary', Static).update(summary_text(pr))
        reviewers = self.query_one('#reviewers', Static)
        reviewers.update(reviewers_text(pr))
        reviewers.border_subtitle = f'{pr.approvals} approved'
        description = resolve_mentions(clean(pr.description), self.names)
        await self.query_one('#description', Markdown).update(description or '_No description_')

        threads = comment_threads(comments)
        self.inline_threads = [t for t in threads if t[0][0].is_inline]
        await self.show_general_comments([t for t in threads if not t[0][0].is_inline])

        self.diffstat = diffstat
        self.file_diffs = {
            path: file_diff for file_diff in parse_diff(clean(diff)) for path in file_diff.paths
        }
        self.show_file_chooser()

    def show_checks(
        self,
        pr: PullRequest,
        diffstat: list[DiffStat],
        statuses: 'list[BuildStatus] | BitbucketError',
        open_tasks: 'int | BitbucketError',
    ) -> None:
        status_list = statuses if isinstance(statuses, list) else None
        task_count = open_tasks if isinstance(open_tasks, int) else None
        self.statuses = status_list or []

        merge = self.query_one('#merge-checks', Static)
        if pr.state == 'OPEN':
            checks = merge_checks(pr, diffstat, status_list, task_count)
            state, label = verdict(checks)
            merge.update(checks_text(checks))
            merge.border_subtitle = Text(label, style=CHECK_MARKS[state][1])
        else:
            merge.update(Text(f'{pr.state.title()}', style='dim'))
            merge.border_subtitle = ''

        builds = self.query_one('#builds', Static)
        error = None if status_list is not None else str(statuses)
        builds.update(builds_text(status_list, error))
        builds.border_subtitle = 'p opens' if any(s.url for s in self.statuses) else ''

    async def show_general_comments(self, threads: list[Thread]) -> None:
        container = self.query_one('#general-comments', Vertical)
        await container.remove_children()
        if threads:
            await container.mount_all(
                CommentView(comment, depth, self.names) for t in threads for comment, depth in t
            )
        else:
            await container.mount(Static(Text('No general comments', style='dim')))
        inline = sum(len(t) for t in self.inline_threads)
        container.border_subtitle = f'{inline} more inline on the diff' if inline else ''

    def threads_for(self, stat: DiffStat) -> list[Thread]:
        paths = {p for p in (stat.old_path, stat.new_path) if p}
        return [t for t in self.inline_threads if t[0][0].path in paths]

    def show_file_chooser(self) -> None:
        chooser = self.query_one('#file-chooser', DataTable)
        row = chooser.cursor_row
        chooser.clear()
        for index, stat in enumerate(self.diffstat):
            letter = stat.status_letter
            comments = sum(len(t) for t in self.threads_for(stat))
            chooser.add_row(
                Text(letter, style=f'bold {STATUS_STYLES.get(letter, "magenta")}'),
                Text(one_line(stat.path)),
                Text(f'+{stat.lines_added}', style='green'),
                Text(f'−{stat.lines_removed}', style='red'),
                Text(str(comments) if comments else ''),
                key=str(index),
            )
        added = sum(s.lines_added for s in self.diffstat)
        removed = sum(s.lines_removed for s in self.diffstat)
        chooser.border_subtitle = f'{len(self.diffstat)} files · +{added} −{removed}'
        self.query_one(TabbedContent).get_tab('diff').label = f'Diff ({len(self.diffstat)})'
        if self.diffstat:
            chooser.move_cursor(row=min(row, len(self.diffstat) - 1))
            self.show_file(chooser.cursor_row)

    @on(DataTable.RowHighlighted, '#file-chooser')
    def file_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.show_file(event.cursor_row)

    @on(DataTable.RowSelected, '#file-chooser')
    def file_selected(self, event: DataTable.RowSelected) -> None:
        self.query_one(DiffView).focus()

    @work(exclusive=True, group='file', exit_on_error=False)
    async def show_file(self, index: int) -> None:
        # Exclusive workers cancel the previous one, so this debounces fast cursor movement.
        await asyncio.sleep(0.08)
        if not 0 <= index < len(self.diffstat):
            return
        stat = self.diffstat[index]
        file_diff = self.file_diffs.get(stat.new_path or '') or self.file_diffs.get(
            stat.old_path or ''
        )
        await self.query_one(DiffView).show_file(
            stat, file_diff, self.threads_for(stat), self.names
        )

    def action_step_file(self, step: int) -> None:
        self.query_one(TabbedContent).active = 'diff'
        chooser = self.query_one('#file-chooser', DataTable)
        if chooser.row_count:
            chooser.move_cursor(row=max(0, min(chooser.cursor_row + step, chooser.row_count - 1)))

    @on(TabbedContent.TabActivated)
    def tab_activated(self, event: TabbedContent.TabActivated) -> None:
        # Put focus in the tab's content so keys act on it rather than on the tab bar.
        target = '#file-chooser' if event.pane.id == 'diff' else '#overview-scroll'
        self.query_one(target).focus()

    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def action_refresh(self) -> None:
        self.load_pull_request()

    def action_open_build(self) -> None:
        """Open the most relevant build: a failing one, else a running one, else the latest."""
        with_urls = [s for s in self.statuses if s.url]
        for states in (('FAILED', 'STOPPED'), ('INPROGRESS',), None):
            for status in with_urls:
                if states is None or status.state in states:
                    self.app.open_url(status.url or '')
                    return
        self.notify('No builds to open')

    def action_open_in_browser(self) -> None:
        if self.pull_request and self.pull_request.html_url:
            self.app.open_url(self.pull_request.html_url)
