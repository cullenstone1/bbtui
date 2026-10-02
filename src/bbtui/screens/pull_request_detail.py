import asyncio
import re

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

from bbtui.api import BitbucketError, PermissionDeniedError
from bbtui.diff import FileDiff, parse_diff
from bbtui.history import Event
from bbtui.merge import Check, merge_checks, verdict
from bbtui.models import BuildStatus, Comment, DiffStat, PullRequest
from bbtui.screens.base import BaseScreen
from bbtui.screens.composer import CommentComposer, CommentTarget
from bbtui.screens.confirm import ConfirmScreen
from bbtui.screens.merge import MergeChoice, MergeScreen
from bbtui.screens.pipeline_run import PipelineRunScreen
from bbtui.screens.url import UrlScreen
from bbtui.text import ago, clean, one_line, timestamp
from bbtui.widgets import CommentView, DiffView, comment_threads, resolve_mentions, thread_views
from bbtui.widgets.comments import Thread

WRITE_SCOPE_HINT = 'the API token needs the write:pullrequest:bitbucket scope'

REVIEW_MARKS = {
    'approved': ('✔', 'green'),
    'changes_requested': ('✗', 'red'),
}
PIPELINE_URL = re.compile(
    r'bitbucket\.org/(?P<workspace>[^/]+)/(?P<slug>[^/]+)/pipelines/results/(?P<number>\d+)'
)
STATUS_STYLES = {'A': 'green', 'D': 'red', 'R': 'yellow', 'M': 'blue'}
CHECK_MARKS = {'ok': ('✔', 'green'), 'blocked': ('✗', 'red'), 'pending': ('●', 'yellow')}
EVENT_MARKS = {
    'opened': ('●', 'cyan'),
    'pushed': ('↑', 'blue'),
    'approved': ('✔', 'green'),
    'changes': ('✗', 'red'),
    'merged': ('●', 'magenta'),
    'declined': ('●', 'red'),
    'update': ('·', 'dim'),
}
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


def history_text(events: 'list[Event] | BitbucketError') -> Text:
    if not isinstance(events, list):
        return Text(f'Unavailable: {one_line(str(events))}', style='dim')
    if not events:
        return Text('No history', style='dim')
    text = Text()
    for event in events:
        mark, style = EVENT_MARKS.get(event.kind, EVENT_MARKS['update'])
        text.append(f'{timestamp(event.date)}  ', style='dim')
        text.append(f'{mark} ', style=style)
        text.append(one_line(event.user.display_name), style='bold')
        action = one_line(event.action)
        if event.count > 1:
            action = f'pushed {event.count} times, latest {action.removeprefix("pushed ")}'
        text.append(f' {action}\n')
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
        Binding('u', 'show_url', 'URL'),
        Binding('p', 'open_build', 'Build'),
        Binding('a', 'toggle_review("approve")', 'Approve'),
        Binding('x', 'toggle_review("changes")', 'Request changes'),
        Binding('c', 'comment', 'Comment'),
        Binding('d', 'toggle_draft', 'Ready/draft'),
        Binding('m', 'merge', 'Merge'),
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
        self.shown_file: int | None = None
        self.checks: list[Check] = []
        self.drafts: dict[tuple, str] = {}

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
                    yield Static(id='history')
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
        self.query_one('#history').border_title = 'History'
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
            pr, diffstat, comments, diff, statuses, open_tasks, history = await asyncio.gather(
                self.api.pull_request(ws, slug, pr_id),
                self.api.pull_request_diffstat(ws, slug, pr_id),
                self.api.pull_request_comments(ws, slug, pr_id),
                self.api.pull_request_diff(ws, slug, pr_id),
                _optional(self.api.pull_request_statuses(ws, slug, pr_id)),
                _optional(self.api.pull_request_open_task_count(ws, slug, pr_id)),
                _optional(self.api.pull_request_history(ws, slug, pr_id)),
            )
        except Exception as exc:
            self.report_error(exc, 'Loading pull request')
            return
        finally:
            tabs.loading = False
        self.diffstat = diffstat
        self.file_diffs = {
            path: file_diff for file_diff in parse_diff(clean(diff)) for path in file_diff.paths
        }
        self.names = known_names(pr, comments)
        await self.show_overview(pr, statuses, open_tasks, history)
        await self.show_comments(comments)

    async def reload_overview(self) -> None:
        """Re-fetch the pull request, builds, tasks and history (after a review action)."""
        ws, slug, pr_id = self.workspace, self.repo_slug, self.pr_id
        try:
            pr, statuses, open_tasks, history = await asyncio.gather(
                self.api.pull_request(ws, slug, pr_id),
                _optional(self.api.pull_request_statuses(ws, slug, pr_id)),
                _optional(self.api.pull_request_open_task_count(ws, slug, pr_id)),
                _optional(self.api.pull_request_history(ws, slug, pr_id)),
            )
        except Exception as exc:
            self.report_error(exc, 'Reloading pull request')
            return
        await self.show_overview(pr, statuses, open_tasks, history)

    async def reload_comments(self) -> None:
        """Re-fetch comments (after posting one), keeping the diff where it was."""
        try:
            comments = await self.api.pull_request_comments(
                self.workspace, self.repo_slug, self.pr_id
            )
        except Exception as exc:
            self.report_error(exc, 'Reloading comments')
            return
        if self.pull_request:
            self.names = known_names(self.pull_request, comments)
        await self.show_comments(comments)

    async def show_overview(
        self,
        pr: PullRequest,
        statuses: 'list[BuildStatus] | BitbucketError',
        open_tasks: 'int | BitbucketError',
        history: 'list[Event] | BitbucketError',
    ) -> None:
        self.pull_request = pr
        self.show_checks(pr, self.diffstat, statuses, open_tasks)
        self.query_one('#summary', Static).update(summary_text(pr))
        reviewers = self.query_one('#reviewers', Static)
        reviewers.update(reviewers_text(pr))
        reviewers.border_subtitle = f'{pr.approvals} approved'
        self.query_one('#history', Static).update(history_text(history))
        description = resolve_mentions(clean(pr.description), self.names)
        await self.query_one('#description', Markdown).update(description or '_No description_')

    async def show_comments(self, comments: list[Comment]) -> None:
        threads = comment_threads(comments)
        self.inline_threads = [t for t in threads if t[0][0].is_inline]
        await self.show_general_comments([t for t in threads if not t[0][0].is_inline])
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
            self.checks = checks
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
            await container.mount_all(view for t in threads for view in thread_views(t, self.names))
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
        # Rebuilding the chooser (after posting a comment) first highlights row 0, then moves back
        # to the current row; skip highlights the cursor has already left, and don't reset the
        # view of the file that's already shown.
        if event.cursor_row != event.data_table.cursor_row:
            return
        if event.cursor_row != self.shown_file:
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
        same_file = index == self.shown_file
        self.shown_file = index
        await self.query_one(DiffView).show_file(
            stat, file_diff, self.threads_for(stat), self.names, keep_position=same_file
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

    def is_editing(self) -> bool:
        """Whether there are unposted comment drafts, which leaving the screen would lose."""
        return bool(self.drafts)

    def action_open_build(self) -> None:
        """Open the most relevant build: a failing one, else a running one, else the latest."""
        with_urls = [s for s in self.statuses if s.url]
        for states in (('FAILED', 'STOPPED'), ('INPROGRESS',), None):
            for status in with_urls:
                if states is None or status.state in states:
                    self.open_build(status.url or '')
                    return
        self.notify('No builds to open')

    # --- reviewing ----------------------------------------------------------------------------

    def report_write_error(self, exc: Exception, action: str) -> None:
        if isinstance(exc, PermissionDeniedError):
            exc = PermissionDeniedError(f'{exc} ({WRITE_SCOPE_HINT})', exc.status_code)
        self.report_error(exc, action)

    def action_toggle_review(self, kind: str) -> None:
        self.toggle_review(kind)

    @work(exclusive=True, group='review', exit_on_error=False)
    async def toggle_review(self, kind: str) -> None:
        """Approve / unapprove, or request changes / withdraw the request."""
        pr = self.pull_request
        if pr is None:
            return
        ws, slug, pr_id = self.workspace, self.repo_slug, self.pr_id
        try:
            me = await self.bbtui.current_user()
            mine = next((p for p in pr.participants if p.user.account_id == me.account_id), None)
            if kind == 'approve':
                if mine and mine.approved:
                    await self.api.unapprove(ws, slug, pr_id)
                    message = 'Approval removed'
                else:
                    await self.api.approve(ws, slug, pr_id)
                    message = 'Approved'
            elif mine and mine.state == 'changes_requested':
                await self.api.remove_request_changes(ws, slug, pr_id)
                message = 'Change request withdrawn'
            else:
                await self.api.request_changes(ws, slug, pr_id)
                message = 'Changes requested'
        except Exception as exc:
            self.report_write_error(exc, 'Updating your review')
            return
        self.notify(message)
        await self.reload_overview()

    def comment_target(self) -> CommentTarget | str:
        """What `c` comments on, from the focus: a reply to the focused comment, an inline
        comment on the diff cursor line, or a general comment. A string explains why not."""
        focused = self.focused
        if isinstance(focused, CommentView):
            parent = focused.comment
            if parent.deleted:
                return "Can't reply to a deleted comment"
            author = one_line(parent.author.display_name)
            quote = '\n'.join(clean(parent.body).strip().split('\n')[:4])
            return CommentTarget(f'Reply to {author}', quote, parent_id=parent.id)
        if isinstance(focused, DiffView):
            line = focused.line_target()
            if line is None:
                return 'Move the cursor to a code line to comment on it'
            return CommentTarget(
                f'Comment on {one_line(line.path)}:{line.number}',
                line.text,
                path=line.path,
                line_to=line.line_to,
                line_from=line.line_from,
            )
        if self.query_one(TabbedContent).active == 'overview':
            return CommentTarget(f'Comment on #{self.pr_id}')
        return 'Press Enter to move into the diff and pick a line to comment on'

    def action_comment(self) -> None:
        target = self.comment_target()
        if isinstance(target, str):
            self.notify(target)
            return

        def finished(result: tuple[str, str] | None) -> None:
            action, text = result or ('cancel', '')
            if action == 'post':
                self.post_comment(target, text)
            elif text.strip():
                self.drafts[target.key] = text
                self.notify('Draft kept; press c on the same spot to continue it')
            else:
                self.drafts.pop(target.key, None)

        self.app.push_screen(CommentComposer(target, self.drafts.get(target.key, '')), finished)

    @work(group='post', exit_on_error=False)
    async def post_comment(self, target: CommentTarget, text: str) -> None:
        try:
            await self.api.create_comment(
                self.workspace,
                self.repo_slug,
                self.pr_id,
                text,
                path=target.path,
                line_to=target.line_to,
                line_from=target.line_from,
                parent_id=target.parent_id,
            )
        except Exception as exc:
            self.drafts[target.key] = text
            self.report_write_error(exc, 'Posting comment (kept as a draft)')
            return
        self.drafts.pop(target.key, None)
        self.notify('Comment posted')
        await self.reload_comments()

    def open_build(self, url: str) -> None:
        """Open a Bitbucket Pipelines run in bbtui; anything else (other CI) in the browser."""
        if match := PIPELINE_URL.search(url):
            label = f'#{self.pr_id} {self.pull_request.title}' if self.pull_request else ''
            self.app.push_screen(
                PipelineRunScreen(match['workspace'], match['slug'], int(match['number']), label)
            )
        else:
            self.app.open_url(url)

    # --- draft and merge ----------------------------------------------------------------------

    def action_toggle_draft(self) -> None:
        pr = self.pull_request
        if pr is None:
            return
        if pr.state != 'OPEN':
            self.notify(f'This pull request is {pr.state.lower()}')
            return
        if pr.draft:
            title, question, label = (
                f'Mark #{pr.id} ready',
                'Mark this pull request ready for review? Reviewers will be notified.',
                'Mark ready',
            )
        else:
            title, question, label = (
                f'Convert #{pr.id} to a draft',
                'Convert this pull request back to a draft?',
                'Convert to draft',
            )

        def confirmed(yes: bool | None) -> None:
            if yes:
                self.set_draft(not pr.draft)

        self.app.push_screen(ConfirmScreen(title, question, label), confirmed)

    @work(exclusive=True, group='review', exit_on_error=False)
    async def set_draft(self, draft: bool) -> None:
        pr = self.pull_request
        if pr is None:
            return
        try:
            await self.api.set_draft(self.workspace, self.repo_slug, pr.id, pr.title, draft)
        except Exception as exc:
            self.report_write_error(exc, 'Updating the pull request')
            return
        self.notify('Converted to a draft' if draft else 'Marked ready for review')
        await self.reload_overview()

    def action_merge(self) -> None:
        pr = self.pull_request
        if pr is None:
            return
        if pr.state != 'OPEN':
            self.notify(f'This pull request is already {pr.state.lower()}')
            return
        if pr.draft:
            self.notify('This pull request is a draft; press d to mark it ready first')
            return
        self.open_merge_dialog(pr)

    @work(exclusive=True, group='review', exit_on_error=False)
    async def open_merge_dialog(self, pr: PullRequest) -> None:
        try:
            strategies, default = await self.api.merge_strategies(
                self.workspace, self.repo_slug, pr.destination_branch
            )
        except BitbucketError:
            strategies, default = [], None  # Fall back to a plain merge commit.

        def chosen(choice: MergeChoice | None) -> None:
            if choice:
                self.merge(pr, choice)

        self.app.push_screen(MergeScreen(pr, self.checks, strategies, default), chosen)

    @work(exclusive=True, group='review', exit_on_error=False)
    async def merge(self, pr: PullRequest, choice: MergeChoice) -> None:
        self.notify(f'Merging #{pr.id}…')
        try:
            await self.api.merge_pull_request(
                self.workspace,
                self.repo_slug,
                pr.id,
                strategy=choice.strategy,
                message=choice.message,
                close_source_branch=choice.close_source_branch,
            )
        except Exception as exc:
            self.report_write_error(exc, 'Merging')
            return
        self.notify(f'Merged #{pr.id} into {pr.destination_branch}', markup=False)
        await self.reload_overview()

    def action_show_url(self) -> None:
        pr = self.pull_request
        if pr is None:
            url = f'https://bitbucket.org/{self.workspace}/{self.repo_slug}/pull-requests/{self.pr_id}'
            self.app.push_screen(UrlScreen(f'#{self.pr_id}', url))
        else:
            self.app.push_screen(UrlScreen(f'#{pr.id} {one_line(pr.title)}', pr.url))
