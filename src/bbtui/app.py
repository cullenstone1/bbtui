from textual import work
from textual.app import App
from textual.binding import Binding

from bbtui.api import BitbucketAPI
from bbtui.config import Settings
from bbtui.models import User
from bbtui.screens import DashboardScreen
from bbtui.watch import WatchedBuild

WATCH_SECONDS = 30
FAILED_STATES = ('FAILED', 'STOPPED', 'ERROR')


class BBTUI(App):
    TITLE = 'bbtui'
    CSS_PATH = 'bbtui.tcss'
    BINDINGS = [Binding('q', 'quit', 'Quit')]

    def __init__(self, settings: Settings, api: BitbucketAPI):
        super().__init__()
        self.settings = settings
        self.api = api
        self._current_user: User | None = None
        self.watched_builds: dict[str, WatchedBuild] = {}
        """Running builds by URL; checked every WATCH_SECONDS."""

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
        self.set_interval(WATCH_SECONDS, self.check_watched_builds)

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
