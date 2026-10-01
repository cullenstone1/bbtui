import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static

from bbtui.api import BitbucketError, PermissionDeniedError
from bbtui.logs import LineDecoder, plain
from bbtui.models import Pipeline, PipelineStep
from bbtui.screens.base import BaseScreen
from bbtui.screens.confirm import ConfirmScreen
from bbtui.text import duration, one_line, relative, timestamp
from bbtui.widgets.log_view import LogView

POLL_SECONDS = 5
MAX_FAILURES_LISTED = 50
WRITE_SCOPE_HINT = 'the API token needs the write:pipeline:bitbucket scope'

STATUS_STYLES = {
    'SUCCESSFUL': ('✔', 'green'),
    'FAILED': ('✗', 'red'),
    'ERROR': ('✗', 'red'),
    'STOPPED': ('■', 'yellow'),
    'EXPIRED': ('■', 'dim'),
    'RUNNING': ('●', 'yellow'),
    'PENDING': ('○', 'yellow'),
    'PAUSED': ('‖', 'yellow'),
    'HALTED': ('‖', 'yellow'),
}


def status_text(status: str) -> Text:
    mark, style = STATUS_STYLES.get(status, ('·', 'dim'))
    return Text(f'{mark} {status.lower()}', style=style)


def run_url(workspace: str, repo_slug: str, build_number: int) -> str:
    return f'https://bitbucket.org/{workspace}/{repo_slug}/pipelines/results/{build_number}'


def summary_text(run: Pipeline) -> Text:
    text = Text()
    text.append(one_line(run.description) + '\n', style='bold')
    text.append_text(status_text(run.status))
    who = one_line(run.creator.display_name) if run.creator else 'unknown'
    text.append(f'  {run.trigger.lower() or "run"} by {who}', style='dim')
    if run.commit:
        text.append(f'  {run.commit[:7]}', style='yellow')
    text.append('\n')
    text.append(f'Started {timestamp(run.created_on)} ({relative(run.created_on)})', style='dim')
    if run.duration_seconds:
        text.append(f' · took {duration(run.duration_seconds)}', style='dim')
    return text


def default_step(steps: list[PipelineStep]) -> int:
    """The step to show first: a failed one, else a running one, else the last."""
    for wanted in (('FAILED', 'ERROR'), ('RUNNING', 'PENDING', 'PAUSED')):
        for index, step in enumerate(steps):
            if step.status in wanted:
                return index
    return max(0, len(steps) - 1)


class PipelineRunScreen(BaseScreen):
    """One pipeline run: summary, steps, likely failures and the selected step's log."""

    BINDINGS = [
        Binding('escape', 'back', 'Back'),
        Binding('r', 'refresh', 'Refresh'),
        Binding('R', 'rerun', 'Re-run'),
        Binding('s', 'stop', 'Stop'),
        Binding('slash', 'search', 'Search log'),
        Binding('o', 'open_in_browser', 'Open in browser'),
    ]

    def __init__(self, workspace: str, repo_slug: str, build_number: int, label: str = ''):
        super().__init__()
        self.workspace = workspace
        self.repo_slug = repo_slug
        self.build_number = build_number
        self.label = label
        """What the run is for, e.g. a pull request title; used in the finish notification."""
        self.run: Pipeline | None = None
        self.steps: list[PipelineStep] = []
        self.step_index: int | None = None
        self.log_offset = 0
        self.decoder = LineDecoder()
        self.poller = None

    @property
    def url(self) -> str:
        return run_url(self.workspace, self.repo_slug, self.build_number)

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id='run-top'):
            yield Static(id='run-summary')
            yield DataTable(id='run-steps', cursor_type='row')
            yield DataTable(id='run-failures', cursor_type='row')
        yield LogView(id='run-log')
        yield Input(id='log-search', placeholder='Search the log, Enter to find, Esc to close')
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f'{self.workspace}/{self.repo_slug} · pipeline #{self.build_number}'
        self.query_one('#run-summary').border_title = f'Pipeline #{self.build_number}'
        steps = self.query_one('#run-steps', DataTable)
        steps.border_title = 'Steps'
        for key in ('Step', 'Status', 'Duration'):
            steps.add_column(key, key=key.lower())
        failures = self.query_one('#run-failures', DataTable)
        failures.border_title = 'Likely failures'
        failures.add_column('Line', key='line')
        failures.add_column('Text', key='text')
        failures.display = False
        self.query_one('#log-search').display = False
        self.query_one('#run-log').border_title = 'Log'
        self.load_run()

    # --- loading ----------------------------------------------------------------------------------

    @work(exclusive=True, group='run', exit_on_error=False)
    async def load_run(self) -> None:
        try:
            run = await self.api.pipeline(self.workspace, self.repo_slug, self.build_number)
            steps = await self.api.pipeline_steps(self.workspace, self.repo_slug, run.uuid)
        except Exception as exc:
            self.report_error(exc, 'Loading pipeline')
            return
        self.show_run(run, steps)
        if steps:
            index = self.step_index if self.step_index is not None else default_step(steps)
            self.query_one('#run-steps', DataTable).move_cursor(row=index)
            await self.load_step(index)
        if run.is_running:
            self.start_polling()

    def show_run(self, run: Pipeline, steps: list[PipelineStep]) -> None:
        self.run, self.steps = run, steps
        summary = self.query_one('#run-summary', Static)
        summary.update(summary_text(run))
        summary.border_subtitle = status_text(run.status)
        table = self.query_one('#run-steps', DataTable)
        row = table.cursor_row
        table.clear()
        for step in steps:
            elapsed = step.duration_seconds
            table.add_row(Text(one_line(step.name)), status_text(step.status), duration(elapsed))
        table.display = len(steps) > 1
        if steps:
            table.move_cursor(row=min(row, len(steps) - 1), animate=False)

    async def load_step(self, index: int) -> None:
        """Load step `index`'s whole log into the view."""
        if not 0 <= index < len(self.steps):
            return
        self.step_index = index
        step = self.steps[index]
        log = self.query_one(LogView)
        log.clear()
        log.border_title = Text(f'Log · {one_line(step.name)}')
        log.loading = True
        self.log_offset = 0
        self.decoder = LineDecoder()
        try:
            await self.tail_log(final=not step.is_running)
        finally:
            log.loading = False
        self.show_failures()
        if self.run and self.run.status in ('FAILED', 'ERROR') and log.failures:
            # The last likely failure is usually the cause; earlier ones are often noise.
            log.go_to(log.failures[-1])
        log.focus()

    async def tail_log(self, final: bool) -> None:
        """Fetch the log from where we left off and append the new lines."""
        if self.run is None or self.step_index is None:
            return
        step = self.steps[self.step_index]
        try:
            data, total = await self.api.step_log(
                self.workspace, self.repo_slug, self.run.uuid, step.uuid, self.log_offset
            )
        except Exception as exc:
            self.report_error(exc, 'Loading log')
            return
        self.log_offset += len(data)
        lines = self.decoder.feed(data)
        if final:
            lines += self.decoder.finish()
        log = self.query_one(LogView)
        log.append(lines)
        state = 'following' if step.is_running else 'complete'
        log.border_subtitle = f'{len(log.lines)} lines · {state}'

    def show_failures(self) -> None:
        log = self.query_one(LogView)
        table = self.query_one('#run-failures', DataTable)
        table.clear()
        for index in log.failures[-MAX_FAILURES_LISTED:]:
            table.add_row(
                Text(str(index + 1), style='dim'),
                Text(one_line(plain(log.lines[index])), style='red'),
                key=str(index),
            )
        table.display = bool(log.failures)
        count = len(log.failures)
        table.border_subtitle = f'{count} · e / E to step through' if count else ''
        if count:
            table.move_cursor(row=table.row_count - 1, animate=False)

    # --- polling ----------------------------------------------------------------------------------

    def start_polling(self) -> None:
        if self.poller is None:
            self.poller = self.set_interval(POLL_SECONDS, self.poll)

    def stop_polling(self) -> None:
        if self.poller is not None:
            self.poller.stop()
            self.poller = None

    @work(exclusive=True, group='poll', exit_on_error=False)
    async def poll(self) -> None:
        if self.run is None:
            return
        try:
            run, steps = await asyncio.gather(
                self.api.pipeline(self.workspace, self.repo_slug, self.build_number),
                self.api.pipeline_steps(self.workspace, self.repo_slug, self.run.uuid),
            )
        except BitbucketError:
            return  # Try again next time.
        previous_step = self.steps[self.step_index] if self.step_index is not None else None
        self.show_run(run, steps)
        if self.step_index is not None and self.step_index < len(steps):
            step = steps[self.step_index]
            await self.tail_log(final=not step.is_running)
            if previous_step and previous_step.is_running and not step.is_running:
                self.show_failures()
        if not run.is_running:
            self.stop_polling()
            self.bbtui.build_finished(self.url, run.status, self.label or run.description)

    # --- interaction ------------------------------------------------------------------------------

    @on(DataTable.RowSelected, '#run-steps')
    async def step_selected(self, event: DataTable.RowSelected) -> None:
        await self.load_step(event.cursor_row)

    @on(DataTable.RowSelected, '#run-failures')
    def failure_selected(self, event: DataTable.RowSelected) -> None:
        log = self.query_one(LogView)
        log.go_to(int(str(event.row_key.value)))
        log.focus()

    def action_search(self) -> None:
        search = self.query_one('#log-search', Input)
        search.display = True
        search.focus()

    @on(Input.Submitted, '#log-search')
    def search_submitted(self, event: Input.Submitted) -> None:
        log = self.query_one(LogView)
        count = log.search(event.value)
        if count:
            self.notify(f'{count} matches; n / N for next / previous')
        log.focus()

    def action_back(self) -> None:
        search = self.query_one('#log-search', Input)
        if search.display:
            search.display = False
            self.query_one(LogView).focus()
        else:
            self.app.pop_screen()

    def action_refresh(self) -> None:
        self.load_run()

    def action_open_in_browser(self) -> None:
        self.app.open_url(self.url)

    def action_rerun(self) -> None:
        run = self.run
        if run is None:
            return

        def confirmed(yes: bool | None) -> None:
            if yes:
                self.rerun(run)

        self.app.push_screen(
            ConfirmScreen(
                f'Re-run #{run.build_number}',
                f'Start a new run of {run.description}?\n'
                f'The last run took {duration(run.duration_seconds) or "unknown"}.',
                'Re-run',
            ),
            confirmed,
        )

    @work(exclusive=True, group='write', exit_on_error=False)
    async def rerun(self, run: Pipeline) -> None:
        try:
            new = await self.api.rerun_pipeline(self.workspace, self.repo_slug, run)
        except Exception as exc:
            self.report_write_error(exc, 'Re-running pipeline')
            return
        self.notify(f'Started #{new.build_number}')
        if new.build_number:
            self.app.switch_screen(
                PipelineRunScreen(self.workspace, self.repo_slug, new.build_number, self.label)
            )

    def action_stop(self) -> None:
        run = self.run
        if run is None or not run.is_running:
            self.notify('This run is not running')
            return

        def confirmed(yes: bool | None) -> None:
            if yes:
                self.stop(run)

        self.app.push_screen(
            ConfirmScreen(f'Stop #{run.build_number}', f'Stop {run.description}?', 'Stop pipeline'),
            confirmed,
        )

    @work(exclusive=True, group='write', exit_on_error=False)
    async def stop(self, run: Pipeline) -> None:
        try:
            await self.api.stop_pipeline(self.workspace, self.repo_slug, run.uuid)
        except Exception as exc:
            self.report_write_error(exc, 'Stopping pipeline')
            return
        self.notify(f'Stopping #{run.build_number}')
        self.poll()

    def report_write_error(self, exc: Exception, action: str) -> None:
        if isinstance(exc, PermissionDeniedError):
            exc = PermissionDeniedError(f'{exc} ({WRITE_SCOPE_HINT})', exc.status_code)
        self.report_error(exc, action)
