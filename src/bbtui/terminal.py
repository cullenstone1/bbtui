"""Picking a colour mode that survives the path to the screen.

Inside tmux, programs often see COLORTERM=truecolor while tmux itself can't pass 24-bit colour
on to the outer terminal (no `RGB` terminal feature). tmux then converts every colour to the
256-colour palette with its own matching, which turns subtle tints (like diff backgrounds) into
greys. Detecting that and letting Textual send 256 colours itself keeps them green and red.
"""

import os
import subprocess

TMUX_FORMAT = '#{client_termname}\t#{client_termfeatures}'


def tmux_client() -> tuple[str, set[str]] | None:
    """The tmux client's terminal name and features, when running inside tmux."""
    if not os.environ.get('TMUX'):
        return None
    try:
        result = subprocess.run(
            ['tmux', 'display-message', '-p', TMUX_FORMAT],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or '\t' not in result.stdout:
        return None
    name, features = result.stdout.strip('\n').split('\t', 1)
    return name, {feature for feature in features.split(',') if feature}


def colour_override(environ: dict[str, str] | None = None) -> tuple[str, str] | None:
    """`(color_system, reason)` to force, or None to let Textual detect it."""
    environ = os.environ if environ is None else environ
    if environ.get('TEXTUAL_COLOR_SYSTEM'):
        return None  # The user chose.
    client = tmux_client()
    if client is None:
        return None
    name, features = client
    if not name or 'RGB' in features:
        return None  # No attached client to judge by, or 24-bit colour gets through.
    if '256' in features or '256color' in name:
        return '256', "tmux isn't passing 24-bit colour to your terminal"
    return 'standard', 'tmux reports a 16-colour terminal'


def apply_colour_override() -> str | None:
    """Set TEXTUAL_COLOR_SYSTEM if needed (before Textual is imported). Returns the reason."""
    override = colour_override()
    if override is None:
        return None
    os.environ['TEXTUAL_COLOR_SYSTEM'], reason = override
    return reason
