from textual.app import App
from textual.binding import Binding

from bbtui.api import BitbucketAPI
from bbtui.config import Settings
from bbtui.screens import DashboardScreen


class BBTUI(App):
    TITLE = 'bbtui'
    CSS_PATH = 'bbtui.tcss'
    BINDINGS = [Binding('q', 'quit', 'Quit')]

    def __init__(self, settings: Settings, api: BitbucketAPI):
        super().__init__()
        self.settings = settings
        self.api = api

    def on_mount(self) -> None:
        if self.settings.theme in self.available_themes:
            self.theme = self.settings.theme
        else:
            self.notify(f'Unknown theme {self.settings.theme!r}', severity='warning', markup=False)
        self.push_screen(DashboardScreen())

    async def on_unmount(self) -> None:
        await self.api.aclose()
