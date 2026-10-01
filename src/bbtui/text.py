"""Helpers for showing untrusted remote text safely.

Remote strings are always wrapped in `rich.text.Text` (never parsed as markup), so a PR title like
`[bold]` can't restyle or break the UI. Control characters are stripped because raw CR/LF and
escape sequences can move the terminal cursor behind the renderer's back.
"""

from datetime import datetime


def one_line(text: str | None) -> str:
    """The first line of `text`, with control characters removed."""
    if not text:
        return ''
    first = text.strip().splitlines()[0] if text.strip() else ''
    return ''.join(c for c in first if c.isprintable())


def truncate(text: str, width: int) -> str:
    """`text` cut to at most `width` characters, ending in an ellipsis when cut."""
    return text if len(text) <= width else text[: max(0, width - 1)] + '…'


def clean(text: str | None) -> str:
    """`text` with control characters removed, keeping line breaks and tabs."""
    if not text:
        return ''
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return '\n'.join(''.join(c for c in line if c.isprintable() or c == '\t') for line in lines)


def ago(when: datetime | None, now: datetime | None = None) -> str:
    """A short relative time like `5m`, `3h`, `2d` or `4mo`."""
    if when is None:
        return ''
    now = now or datetime.now(when.tzinfo)
    seconds = max(0, int((now - when).total_seconds()))
    for unit, size in (
        ('y', 31_536_000),
        ('mo', 2_592_000),
        ('d', 86_400),
        ('h', 3_600),
        ('m', 60),
    ):
        if seconds >= size:
            return f'{seconds // size}{unit}'
    return 'now'


def relative(when: datetime | None, now: datetime | None = None) -> str:
    """A spelled-out relative time like `just now`, `18 min ago` or `3 days ago`."""
    if when is None:
        return ''
    now = now or datetime.now(when.tzinfo)
    seconds = max(0, int((now - when).total_seconds()))
    units = (
        ('year', 31_536_000),
        ('month', 2_592_000),
        ('week', 604_800),
        ('day', 86_400),
        ('hour', 3_600),
        ('min', 60),
    )
    for unit, size in units:
        if seconds >= size:
            count = seconds // size
            plural = 's' if count != 1 and unit != 'min' else ''
            return f'{count} {unit}{plural} ago'
    return 'just now'


def timestamp(when: datetime | None) -> str:
    """A local `YYYY-MM-DD HH:MM` timestamp."""
    return when.astimezone().strftime('%Y-%m-%d %H:%M') if when else ''


def duration(seconds: int | float | None) -> str:
    """`45s`, `20m 2s`, `1h 3m`."""
    if seconds is None:
        return ''
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds}s'
    if seconds < 3600:
        return f'{seconds // 60}m {seconds % 60}s'
    return f'{seconds // 3600}h {seconds % 3600 // 60}m'
