import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Static

from bbtui.api import BitbucketError, NotFoundError
from bbtui.diff import FileDiff, parse_diff
from bbtui.models import Commit, DiffStat, Repository
from bbtui.screens.base import BaseScreen
from bbtui.screens.url import UrlScreen
from bbtui.text import clean, one_line, timestamp
from bbtui.widgets import DiffView
from bbtui.widgets.branch_picker import TAG_STYLE, BranchPicker
from bbtui.widgets.file_chooser import fill_file_chooser, setup_file_chooser

PAGE_SIZE = 30
"""Commits fetched at a time; Bitbucket takes ~2 s for 30 on a long history."""
LOAD_AHEAD = 5
"""Fetch the next page when the cursor is this close to the last loaded commit."""


def commit_url(repo: Repository, commit_hash: str) -> str:
    return f'https://bitbucket.org/{repo.full_name}/commits/{commit_hash}'


class BranchChoiceScreen(ModalScreen[str | None]):
    """A branch picker in a dialog. Dismisses with the branch, `''` for "none" (when allowed),
    or None when cancelled."""

    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, repo: Repository, title: str, hint: str, allow_empty: bool = False):
        super().__init__()
        self.repo = repo
        self.title_text = title
        self.hint = hint
        self.allow_empty = allow_empty

    def compose(self) -> ComposeResult:
        with Vertical(id='branch-dialog'):
            yield BranchPicker(
                self.repo.workspace,
                self.repo.slug,
                allow_empty=self.allow_empty,
                include_tags=True,
            )
            yield Static(Text(self.hint, style='dim'), id='branch-hint')

    def on_mount(self) -> None:
        self.query_one('#branch-dialog').border_title = Text(self.title_text)

    @on(BranchPicker.Chosen)
    def chosen(self, event: BranchPicker.Chosen) -> None:
        self.dismiss(event.branch)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CommitsScreen(BaseScreen):
    """A branch's commits, newest first, optionally only those not on another branch.
    More are fetched as you scroll down."""

    BINDINGS = [
        Binding('escape', 'app.pop_screen', 'Back'),
        Binding('b', 'choose_branch', 'Branch'),
        Binding('x', 'choose_base', 'Not on…'),
        Binding('r', 'refresh', 'Refresh'),
        Binding('u', 'show_url', 'URL'),
    ]

    def __init__(self, repo: Repository, branch: str | None = None, base: str | None = None):
        super().__init__()
        self.repo = repo
        self.branch = branch or repo.main_branch or 'master'
        self.base = base
        """Only show commits that aren't on this branch, when set."""
        self.commits: list[Commit] = []
        self.tags: dict[str, list[str]] | None = None
        """Tag names by commit hash, fetched once (and again on refresh)."""
        self.next_page: str | None = None
        self.loading_more = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id='commits', cursor_type='row', zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f'{self.repo.full_name} · commits'
        table = self.query_one(DataTable)
        for label, key in (
            ('Commit', 'hash'),
            ('Date', 'date'),
            ('Author', 'author'),
            ('Summary', 'summary'),
        ):
            table.add_column(label, key=key)
        self.load_commits()

    def show_title(self) -> None:
        table = self.query_one(DataTable)
        title = Text()
        title.append(one_line(self.branch), style='bold')
        if self.base:
            title.append(f'  not on {one_line(self.base)}')
        table.border_title = title
        count = len(self.commits)
        more = ' · more as you scroll' if self.next_page else ''
        table.border_subtitle = (
            f'{count} commit{"s" if count != 1 else ""}{more} · b branch · x not on'
        )

    @work(exclusive=True, group='commits', exit_on_error=False)
    async def load_commits(self, reload_tags: bool = False) -> None:
        table = self.query_one(DataTable)
        table.clear()
        self.commits, self.next_page = [], None
        self.loading_more = False
        self.show_title()
        table.loading = True
        ws, slug = self.repo.workspace, self.repo.slug
        try:
            (commits, next_page), tags = await asyncio.gather(
                self.api.commits(ws, slug, self.branch, self.base, pagelen=PAGE_SIZE),
                self.load_tags(reload_tags),
            )
        except NotFoundError:
            missing = ' or '.join(repr(b) for b in (self.branch, self.base) if b)
            self.notify(
                f'No branch or tag {missing} in this repository', severity='error', markup=False
            )
            return
        except Exception as exc:
            self.report_error(exc, 'Loading commits')
            return
        finally:
            table.loading = False
        self.tags = tags
        self.add_commits(commits, next_page)
        if not commits:
            what = f'on {self.branch} that are not on {self.base}' if self.base else 'here'
            self.notify(f'No commits {what}', markup=False)

    async def load_tags(self, reload: bool) -> dict[str, list[str]]:
        """Tag names by commit hash. Tags only label commits, so failing to get them is quiet."""
        if self.tags is not None and not reload:
            return self.tags
        try:
            tags = await self.api.tags(self.repo.workspace, self.repo.slug)
        except BitbucketError:
            return {}
        by_commit: dict[str, list[str]] = {}
        for tag in tags:
            by_commit.setdefault(tag.target, []).append(tag.name)
        return by_commit

    def tags_of(self, commit: Commit) -> list[str]:
        return (self.tags or {}).get(commit.hash, [])

    def add_commits(self, commits: list[Commit], next_page: str | None) -> None:
        table = self.query_one(DataTable)
        for commit in commits:
            table.add_row(
                Text(commit.short_hash, style='yellow'),
                Text(timestamp(commit.date), style='dim'),
                Text(one_line(commit.author)),
                summary_text(commit, self.tags_of(commit)),
                key=commit.hash,
            )
        self.commits.extend(commits)
        self.next_page = next_page
        self.show_title()

    @on(DataTable.RowHighlighted, '#commits')
    def highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.next_page and event.cursor_row >= len(self.commits) - LOAD_AHEAD:
            self.load_more()

    @work(group='more', exit_on_error=False)
    async def load_more(self) -> None:
        if self.loading_more or not self.next_page:
            return
        self.loading_more = True
        page = self.next_page
        try:
            commits, next_page = await self.api.commits(
                self.repo.workspace, self.repo.slug, self.branch, self.base, page=page
            )
        except Exception as exc:
            self.report_error(exc, 'Loading more commits')
            return
        finally:
            self.loading_more = False
        if page == self.next_page:  # Not reloaded for another branch meanwhile.
            self.add_commits(commits, next_page)

    def selected_commit(self) -> Commit | None:
        table = self.query_one(DataTable)
        if not self.commits or table.cursor_row >= len(self.commits):
            return None
        return self.commits[table.cursor_row]

    @on(DataTable.RowSelected, '#commits')
    def selected(self, event: DataTable.RowSelected) -> None:
        if commit := self.selected_commit():
            self.app.push_screen(CommitScreen(self.repo, commit, self.tags_of(commit)))

    def action_choose_branch(self) -> None:
        def chosen(branch: str | None) -> None:
            if branch and branch != self.branch:
                self.branch = branch
                self.load_commits()

        self.app.push_screen(
            BranchChoiceScreen(
                self.repo,
                'Show commits on (branch or tag)',
                f'Currently {self.branch}. Type to filter, Enter to pick, Esc to cancel.',
            ),
            chosen,
        )

    def action_choose_base(self) -> None:
        def chosen(base: str | None) -> None:
            if base is not None and (base or None) != self.base:
                self.base = base or None
                self.load_commits()

        current = f'Currently {self.base}.' if self.base else 'Currently showing all commits.'
        self.app.push_screen(
            BranchChoiceScreen(
                self.repo,
                f'Only commits on {self.branch} that are not on',
                f'{current} Enter on an empty filter shows all commits; Esc cancels.',
                allow_empty=True,
            ),
            chosen,
        )

    def action_refresh(self) -> None:
        self.load_commits(reload_tags=True)

    def action_show_url(self) -> None:
        if commit := self.selected_commit():
            title = f'{commit.short_hash} {one_line(commit.summary)}'
            self.app.push_screen(UrlScreen(title, commit_url(self.repo, commit.hash)))
        else:
            url = f'https://bitbucket.org/{self.repo.full_name}/commits/branch/{self.branch}'
            self.app.push_screen(UrlScreen(f'{one_line(self.branch)} commits', url))


def tag_decoration(tags: list[str]) -> Text:
    """`(tag: v1.2, tag: v1.2-rc1) `, as `git log --decorate` shows them; empty without tags."""
    if not tags:
        return Text()
    names = ', '.join(f'tag: {one_line(tag)}' for tag in tags)
    return Text(f'({names}) ', style=TAG_STYLE)


def summary_text(commit: Commit, tags: list[str]) -> Text:
    text = tag_decoration(tags)
    text.append(one_line(commit.summary))
    return text


def commit_text(commit: Commit, tags: list[str] | None = None) -> Text:
    text = tag_decoration(tags or [])
    text.append(one_line(commit.summary) + '\n', style='bold')
    text.append(f'{one_line(commit.author)} · {timestamp(commit.date)}', style='dim')
    if commit.parents:
        label = 'parents' if len(commit.parents) > 1 else 'parent'
        text.append(f' · {label} {" ".join(p[:8] for p in commit.parents)}', style='dim')
    body = clean(commit.message).strip().split('\n', 1)[1:]
    if body and body[0].strip():
        text.append('\n\n' + body[0].strip('\n'))
    return text


class CommitScreen(BaseScreen):
    """One commit: its message, then its changed files and one file's diff at a time."""

    BINDINGS = [
        Binding('escape', 'app.pop_screen', 'Back'),
        Binding('u', 'show_url', 'URL'),
        Binding('left_square_bracket', 'step_file(-1)', 'Prev file'),
        Binding('right_square_bracket', 'step_file(1)', 'Next file'),
    ]

    def __init__(self, repo: Repository, commit: Commit, tags: list[str] | None = None):
        super().__init__()
        self.repo = repo
        self.commit = commit
        self.tags = tags or []
        self.diffstat: list[DiffStat] = []
        self.file_diffs: dict[str, FileDiff] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id='commit-message'):
            yield Static(commit_text(self.commit, self.tags))
        yield DataTable(id='file-chooser', cursor_type='row', zebra_stripes=True)
        yield DiffView(id='diff-view')
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f'{self.repo.full_name} · {self.commit.short_hash}'
        self.query_one('#commit-message').border_title = Text(self.commit.hash)
        self.query_one('#diff-view').border_title = 'Diff'
        setup_file_chooser(self.query_one('#file-chooser', DataTable), comments=False)
        self.load_commit()

    @work(exclusive=True, group='commit', exit_on_error=False)
    async def load_commit(self) -> None:
        chooser = self.query_one('#file-chooser', DataTable)
        chooser.loading = True
        ws, slug, commit = self.repo.workspace, self.repo.slug, self.commit.hash
        try:
            self.diffstat, diff = await asyncio.gather(
                self.api.commit_diffstat(ws, slug, commit), self.api.commit_diff(ws, slug, commit)
            )
        except Exception as exc:
            self.report_error(exc, 'Loading the commit')
            return
        finally:
            chooser.loading = False
        self.file_diffs = {
            path: file_diff for file_diff in parse_diff(clean(diff)) for path in file_diff.paths
        }
        fill_file_chooser(chooser, self.diffstat)
        chooser.focus()
        if self.diffstat:
            self.show_file(0)

    @on(DataTable.RowHighlighted, '#file-chooser')
    def file_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.cursor_row == event.data_table.cursor_row:
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
        await self.query_one(DiffView).show_file(stat, file_diff, [], {})

    def action_step_file(self, step: int) -> None:
        chooser = self.query_one('#file-chooser', DataTable)
        if chooser.row_count:
            chooser.move_cursor(row=max(0, min(chooser.cursor_row + step, chooser.row_count - 1)))

    def action_show_url(self) -> None:
        title = f'{self.commit.short_hash} {one_line(self.commit.summary)}'
        self.app.push_screen(UrlScreen(title, commit_url(self.repo, self.commit.hash)))
