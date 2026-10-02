import argparse
import asyncio
import sys

from bbtui import __version__
from bbtui.api import BitbucketAPI, BitbucketClient, BitbucketError
from bbtui.config import Settings, config_file_path
from bbtui.terminal import apply_colour_override


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


def init() -> int:
    from bbtui.init_config import run_init

    if not sys.stdin.isatty():
        sys.stderr.write('bbtui --init asks questions, so it needs a terminal.\n')
        return 2
    try:
        return run_init(config_file_path())
    except (KeyboardInterrupt, EOFError):
        sys.stderr.write('\nCancelled; nothing written.\n')
        return 130


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='bbtui', description='A terminal UI for Bitbucket Cloud.')
    parser.add_argument('-w', '--workspace', help='workspace to open (overrides the config)')
    parser.add_argument(
        '--init', action='store_true', help='set up credentials and write the config file'
    )
    parser.add_argument(
        '--check', action='store_true', help='verify credentials, list workspaces and exit'
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = parser.parse_args(argv)

    if args.init:
        sys.exit(init())

    settings = Settings()
    if args.workspace:
        settings.workspace = args.workspace
    missing = [m for m in settings.missing_required() if not (args.check and m == 'workspace')]
    if missing:
        path = config_file_path()
        hint = (
            'Run `bbtui --init` to set them up.'
            if not path.exists()
            else f'Set them in {path} (or as BBTUI_* environment variables).'
        )
        sys.stderr.write(f'Missing settings: {", ".join(missing)}.\n{hint}\n')
        sys.exit(2)

    assert settings.username and settings.api_token
    api = BitbucketAPI(
        BitbucketClient(settings.username, settings.api_token.get_secret_value(), settings.base_url)
    )
    if args.check:
        sys.exit(asyncio.run(check(api)))

    # Must happen before Textual is imported: it reads TEXTUAL_COLOR_SYSTEM at import time.
    colour_note = apply_colour_override()

    from bbtui.app import BBTUI

    BBTUI(settings, api, colour_note).run()
