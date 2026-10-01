import argparse
import asyncio
import sys

from bbtui import __version__
from bbtui.api import BitbucketAPI, BitbucketClient, BitbucketError
from bbtui.config import Settings, config_file_path

CONFIG_EXAMPLE = """\
username: you@example.com   # your Atlassian account email
api_token: <scoped API token with Bitbucket scopes>
workspace: your-workspace
"""


async def check(api: BitbucketAPI) -> int:
    try:
        user = await api.current_user()
        workspaces = await api.workspaces()
    except BitbucketError as exc:
        sys.stderr.write(f'{exc}\n')
        return 1
    finally:
        await api.aclose()
    sys.stdout.write(f'Authenticated as {user.display_name}\nWorkspaces:\n')
    for workspace in workspaces:
        admin = ' (admin)' if workspace.is_admin else ''
        sys.stdout.write(f'  {workspace.slug}{admin}\n')
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='bbtui', description='A terminal UI for Bitbucket Cloud.')
    parser.add_argument('-w', '--workspace', help='workspace to open (overrides the config)')
    parser.add_argument(
        '--check', action='store_true', help='verify credentials, list workspaces and exit'
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = parser.parse_args(argv)

    settings = Settings()
    if args.workspace:
        settings.workspace = args.workspace
    missing = [m for m in settings.missing_required() if not (args.check and m == 'workspace')]
    if missing:
        sys.stderr.write(
            f'Missing settings: {", ".join(missing)}.\n'
            f'Set them in {config_file_path()} (or as BBTUI_* environment variables):\n\n'
            f'{CONFIG_EXAMPLE}'
        )
        sys.exit(2)

    assert settings.username and settings.api_token
    api = BitbucketAPI(
        BitbucketClient(settings.username, settings.api_token.get_secret_value(), settings.base_url)
    )
    if args.check:
        sys.exit(asyncio.run(check(api)))

    from bbtui.app import BBTUI

    BBTUI(settings, api).run()
