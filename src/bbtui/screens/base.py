from typing import TYPE_CHECKING, cast

import httpx
from textual.screen import Screen

from bbtui.api import BitbucketAPI, BitbucketError
from bbtui.config import Settings
from bbtui.vim import VIM_BINDINGS, VimNavigation

if TYPE_CHECKING:
    from bbtui.app import BBTUI


class BaseScreen(VimNavigation, Screen):
    BINDINGS = VIM_BINDINGS

    @property
    def bbtui(self) -> 'BBTUI':
        return cast('BBTUI', self.app)

    @property
    def api(self) -> BitbucketAPI:
        return self.bbtui.api

    @property
    def settings(self) -> Settings:
        return self.bbtui.settings

    def report_error(self, exc: Exception, action: str) -> None:
        if isinstance(exc, BitbucketError):
            message = str(exc)
        elif isinstance(exc, httpx.HTTPError):
            message = f'Network error: {exc}'
        else:
            raise exc
        self.notify(f'{action}: {message}', severity='error', timeout=10, markup=False)
