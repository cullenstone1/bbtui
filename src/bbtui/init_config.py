"""`bbtui --init`: ask for credentials, check them, and write the config file."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import getpass
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import sys
import textwrap
from typing import TextIO

from bbtui.api import BitbucketAPI, BitbucketClient, BitbucketError
from bbtui.config import DEFAULT_BASE_URL
from bbtui.models import Workspace

TOKEN_URL = 'https://id.atlassian.com/manage-profile/security/api-tokens'
READ_SCOPES = (
    'read:user:bitbucket',
    'read:workspace:bitbucket',
    'read:repository:bitbucket',
    'read:pullrequest:bitbucket',
    'read:pipeline:bitbucket',
)
WRITE_SCOPES = ('write:pullrequest:bitbucket', 'write:pipeline:bitbucket')

INTRO = f"""\
bbtui needs a scoped Atlassian API token for Bitbucket.

  1. Open {TOKEN_URL}
  2. Choose "Create API token with scopes", name it (e.g. bbtui), pick an expiry,
     and choose the Bitbucket app.
  3. Select these scopes:
{textwrap.fill(', '.join(READ_SCOPES), 90, initial_indent=' ' * 7, subsequent_indent=' ' * 7)}
     and, to approve, comment, merge, re-run and stop pipelines:
       {', '.join(WRITE_SCOPES)}
  4. Copy the token (it is only shown once).

"""


@dataclass(frozen=True)
class Account:
    display_name: str
    workspaces: list[Workspace]


def example_config() -> str:
    """The commented example config shipped with bbtui."""
    return files('bbtui').joinpath('config.example.yaml').read_text(encoding='utf-8')


def _yaml_string(value: str) -> str:
    # A JSON string is a valid double-quoted YAML string, escapes included.
    return json.dumps(value)


def render_config(username: str, api_token: str, workspace: str, starred: list[str]) -> str:
    """The example config with your values filled in, comments kept."""
    text = example_config()
    values = {'username': username, 'api_token': api_token, 'workspace': workspace}
    for key, value in values.items():
        text, count = re.subn(
            rf'^{key}: .*$', lambda _, k=key, v=value: f'{k}: {_yaml_string(v)}', text, flags=re.M
        )
        assert count == 1, key
    if starred:
        listed = ''.join(f'  - {_yaml_string(repo)}\n' for repo in starred)
        text, count = re.subn(
            r'^starred_repos: \[\]\n(?:#  - .*\n)*',
            lambda _: f'starred_repos:\n{listed}',
            text,
            flags=re.M,
        )
        assert count == 1, 'starred_repos'
    return text


def write_private(path: Path, text: str) -> None:
    """Write `text` to `path`, readable by you only (it holds your API token)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        file.write(text)
    path.chmod(0o600)  # In case the file already existed with wider permissions.


async def _fetch_account(username: str, api_token: str, base_url: str) -> Account:
    api = BitbucketAPI(BitbucketClient(username, api_token, base_url))
    try:
        user = await api.current_user()
        return Account(user.display_name, await api.workspaces())
    finally:
        await api.aclose()


def check_account(username: str, api_token: str, base_url: str = DEFAULT_BASE_URL) -> Account:
    """Sign in with the credentials; raises `BitbucketError` if Bitbucket refuses them."""
    return asyncio.run(_fetch_account(username, api_token, base_url))


def _yes(answer: str, default: bool = False) -> bool:
    answer = answer.strip().lower()
    return default if not answer else answer in ('y', 'yes')


def choose_workspace(workspaces: list[Workspace], ask: Callable[[str], str], out: TextIO) -> str:
    if len(workspaces) == 1:
        out.write(f'Workspace: {workspaces[0].slug}\n')
        return workspaces[0].slug
    if workspaces:
        out.write('Your workspaces:\n')
        for number, workspace in enumerate(workspaces, 1):
            out.write(f'  {number}. {workspace.slug}\n')
    slugs = [w.slug for w in workspaces]
    while True:
        answer = ask('Workspace to open (number or slug): ').strip()
        if answer.isdigit() and 1 <= int(answer) <= len(slugs):
            return slugs[int(answer) - 1]
        if answer and (answer in slugs or not slugs):
            return answer
        out.write('Pick one of the numbers or slugs above.\n')


def run_init(
    path: Path,
    ask: Callable[[str], str] = input,
    ask_secret: Callable[[str], str] = getpass.getpass,
    out: TextIO = sys.stdout,
    check: Callable[[str, str], Account] = check_account,
) -> int:
    """Interactively create the config file at `path`. Returns the exit status."""
    if path.exists() and not _yes(ask(f'{path} already exists. Replace it? [y/N] ')):
        out.write('Left it unchanged.\n')
        return 1
    out.write(INTRO)
    username = ''
    while not username:
        username = ask('Atlassian account email: ').strip()
    while True:
        api_token = ask_secret('API token (input hidden): ').strip()
        if not api_token:
            continue
        out.write('Checking…\n')
        try:
            account = check(username, api_token)
            break
        except BitbucketError as exc:
            out.write(f'Bitbucket refused that: {exc}\n')
            if not _yes(ask('Try another token? [Y/n] '), default=True):
                out.write('Nothing written.\n')
                return 1
    out.write(f'Signed in as {account.display_name}.\n')
    workspace = choose_workspace(account.workspaces, ask, out)
    answer = ask('Repositories to pin to the dashboard (comma-separated slugs, optional): ')
    starred = [repo.strip() for repo in answer.split(',') if repo.strip()]
    write_private(path, render_config(username, api_token, workspace, starred))
    out.write(f'\nWrote {path} (readable by you only). Run `bbtui` to start.\n')
    return 0
