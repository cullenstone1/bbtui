"""Copying to the system clipboard.

A clipboard tool is tried first: the terminal escape (OSC 52) is often swallowed, e.g. by tmux
with `set-clipboard off`. OSC 52 is the fallback when no tool works (say, over SSH).
"""

import os
import shutil
import subprocess


def clipboard_commands(environ: dict[str, str] | None = None) -> list[list[str]]:
    """Clipboard commands to try, best first, for the current session."""
    environ = os.environ if environ is None else environ
    commands = []
    if environ.get('WAYLAND_DISPLAY'):
        commands.append(['wl-copy'])
    if environ.get('DISPLAY'):
        commands += [['xclip', '-selection', 'clipboard'], ['xsel', '--clipboard', '--input']]
    commands.append(['pbcopy'])
    return [command for command in commands if shutil.which(command[0])]


def copy_with_tool(text: str) -> str | None:
    """Copy using the first clipboard tool that works; returns its name, or None."""
    for command in clipboard_commands():
        try:
            subprocess.run(
                command,
                input=text.encode(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        return command[0]
    return None
