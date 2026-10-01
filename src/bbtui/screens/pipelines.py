from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header

from bbtui.models import Pipeline, Repository, User, same_user
from bbtui.screens.base import BaseScreen
from bbtui.screens.pipeline_run import PipelineRunScreen, status_text
from bbtui.text import ago, duration, one_line, truncate

POLL_SECONDS = 15


class PipelinesScreen(BaseScreen):
    """A repository's pipeline runs, newest first, filterable to yours or failed ones."""

    BINDINGS = [
        Binding('escape', 'app.pop_screen', 'Back'),
        Binding('r', 'refresh', 'Refresh'),
        Binding('m', 'toggle_filter("mine")', 'Mine'),
        Binding('f', 'toggle_filter("failed")', 'Failed'),
        Binding('o', 'open_in_browser', 'Open in browser'),
    ]

    def __init__(self, repo: Repository):
        super().__init__()
        self.repo = repo
        self.runs: list[Pipeline] = []
        self.filters: set[str] = set()
        self.me: User | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id='pipelines', cursor_type='row', zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f'{self.repo.full_name} · pipelines'
        table = self.query_one(DataTable)
        table.border_title = Text(f'Pipelines · {one_line(self.repo.full_name)}')
        for label, key in (
            ('#', 'number'),
            ('Status', 'status'),
            ('What', 'what'),
            ('Trigger', 'trigger'),
            ('By', 'by'),
            ('Started', 'started'),
            ('Took', 'took'),
        ):
            table.add_column(label, key=key)
        self.load_runs()
        self.set_interval(POLL_SECONDS, self.poll)

    @work(exclusive=True, group='runs', exit_on_error=False)
    async def load_runs(self, quiet: bool = False) -> None:
        table = self.query_one(DataTable)
        table.loading = not quiet
        try:
            if self.me is None:
                self.me = await self.bbtui.current_user()
            self.runs = await self.api.pipelines(self.repo.workspace, self.repo.slug, limit=100)
        except Exception as exc:
            if not quiet:
                self.report_error(exc, 'Loading pipelines')
            return
        finally:
            table.loading = False
        self.show_runs()

    def visible_runs(self) -> list[Pipeline]:
        runs = self.runs
        if 'mine' in self.filters:
            runs = [r for r in runs if r.creator and self.me and same_user(r.creator, self.me)]
        if 'failed' in self.filters:
            runs = [r for r in runs if r.status in ('FAILED', 'ERROR', 'STOPPED')]
        return runs

    def show_runs(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        runs = self.visible_runs()
        for run in runs:
            table.add_row(
                Text(str(run.build_number), style='bold'),
                status_text(run.status),
                Text(truncate(one_line(run.description), 60)),
                Text(run.trigger.lower(), style='dim'),
                Text(truncate(one_line(run.creator.display_name), 20) if run.creator else ''),
                Text(ago(run.created_on)),
                Text(duration(run.duration_seconds)),
                key=str(run.build_number),
            )
        if runs:
            table.move_cursor(row=min(row, len(runs) - 1), animate=False)
        shown = ' · '.join(sorted(self.filters)) or 'all'
        table.border_subtitle = f'{shown} · {len(runs)} of {len(self.runs)}'

    def poll(self) -> None:
        if any(run.is_running for run in self.runs):
            self.load_runs(quiet=True)

    def selected_run(self) -> Pipeline | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return next((r for r in self.runs if str(r.build_number) == row_key.value), None)

    @on(DataTable.RowSelected)
    def open_run(self, event: DataTable.RowSelected) -> None:
        self.app.push_screen(
            PipelineRunScreen(self.repo.workspace, self.repo.slug, int(str(event.row_key.value)))
        )

    def action_toggle_filter(self, name: str) -> None:
        self.filters ^= {name}
        self.show_runs()

    def action_refresh(self) -> None:
        self.load_runs()

    def action_open_in_browser(self) -> None:
        run = self.selected_run()
        suffix = f'/results/{run.build_number}' if run else ''
        self.app.open_url(f'https://bitbucket.org/{self.repo.full_name}/pipelines{suffix}')
