import os
import time

from textual import events, work
from textual.app import App
from textual.binding import Binding

from bbtui.api import BitbucketAPI
from bbtui.config import Settings
from bbtui.models import User
from bbtui.screens import DashboardScreen
from bbtui.watch import WatchedBuild

WATCH_SECONDS = 30
IDLE_CHECK_SECONDS = 15
INTERACTIONS = (
    events.Key,
    events.MouseDown,
    events.MouseScrollDown,
    events.MouseScrollUp,
    events.Paste,
)
"""Input that counts as using the app, for the idle timeout (mouse movement alone doesn't)."""
FAILED_STATES = ('FAILED', 'STOPPED', 'ERROR')


class BBTUI(App):
    TITLE = 'bbtui'
    CSS_PATH = 'bbtui.tcss'
    BINDINGS = [Binding('q', 'quit', 'Quit')]

    def __init__(self, settings: Settings, api: BitbucketAPI, colour_note: str | None = None):
        super().__init__()
        self.settings = settings
        self.api = api
        self.colour_note = colour_note
        """Why the colour mode was reduced at startup, if it was."""
        self._current_user: User | None = None
        self.watched_builds: dict[str, WatchedBuild] = {}
        """Running builds by URL; checked every WATCH_SECONDS."""
        self.last_interaction = time.monotonic()

    async def current_user(self) -> User:
        """The authenticated user, fetched once."""
        if self._current_user is None:
            self._current_user = await self.api.current_user()
        return self._current_user

    def on_mount(self) -> None:
        if self.settings.theme in self.available_themes:
            self.theme = self.settings.theme
        else:
            self.notify(f'Unknown theme {self.settings.theme!r}', severity='warning', markup=False)
        self.push_screen(DashboardScreen())
        full_colour = self.console.color_system in ('256', 'truecolor')
        if not full_colour:
            # Pass named colours (red, green, ...) straight to the terminal's own palette;
            # converting them to RGB first and back down to 16 colours turns red into magenta.
            self.ansi_color = True
        if self.colour_note or not full_colour:
            reason = self.colour_note or (
                f'your terminal reports only 16 colours (TERM={os.environ.get("TERM", "?")})'
            )
            mode = '256 colours' if self.console.color_system == '256' else '16 colours'
            self.notify(
                f'Using {mode}: {reason}. For full colour see "Colours" in the README.',
                severity='warning' if not full_colour else 'information',
                timeout=12,
                markup=False,
            )
        self.set_interval(WATCH_SECONDS, self.check_watched_builds)
        if self.settings.idle_timeout_minutes > 0:
            self.set_interval(IDLE_CHECK_SECONDS, self.check_idle_timeout)

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, INTERACTIONS):
            self.last_interaction = time.monotonic()
        await super().on_event(event)

    def is_editing(self) -> bool:
        """Whether any open screen holds unsaved writing (a comment, a draft, a new PR)."""
        return any(getattr(screen, 'is_editing', lambda: False)() for screen in self.screen_stack)

    async def check_idle_timeout(self) -> None:
        """After `idle_timeout_minutes` without input, close everything above the dashboard."""
        minutes = self.settings.idle_timeout_minutes
        if minutes <= 0 or time.monotonic() - self.last_interaction < minutes * 60:
            return
        dashboard = next((s for s in self.screen_stack if isinstance(s, DashboardScreen)), None)
        if dashboard is None or self.screen is dashboard or self.is_editing():
            return
        while self.screen is not dashboard:
            await self.pop_screen()
        self.last_interaction = time.monotonic()
        self.notify(f'Back to the dashboard after {minutes:g} min idle')

    def watch_build(self, url: str, build: WatchedBuild) -> None:
        self.watched_builds[url] = build

    def build_finished(self, url: str, status: str, label: str) -> None:
        """Tell the user a build finished (once, however it was noticed)."""
        watched = self.watched_builds.pop(url, None)
        label = watched.label if watched else label
        failed = status.upper() in FAILED_STATES
        self.notify(
            f'{"✗ Build failed" if failed else "✔ Build passed"}: {label}',
            severity='error' if failed else 'information',
            timeout=15,
            markup=False,
        )

    @work(exclusive=True, group='watch', exit_on_error=False)
    async def check_watched_builds(self) -> None:
        by_pr: dict[tuple[str, str, int], list[tuple[str, WatchedBuild]]] = {}
        for url, build in self.watched_builds.items():
            by_pr.setdefault((build.workspace, build.repo_slug, build.pr_id), []).append(
                (url, build)
            )
        for (workspace, repo_slug, pr_id), builds in by_pr.items():
            try:
                statuses = await self.api.pull_request_statuses(workspace, repo_slug, pr_id)
            except Exception:
                continue
            states = {status.key: status.state for status in statuses}
            for url, build in builds:
                state = states.get(build.key)
                if state and state != 'INPROGRESS' and url in self.watched_builds:
                    self.build_finished(url, state, build.label)

    async def on_unmount(self) -> None:
        await self.api.aclose()
