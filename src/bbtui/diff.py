"""Parsing unified diffs (as returned by Bitbucket's pull request `/diff`) into per-file lines with
old/new line numbers, so a file can be shown on its own and comments placed on their lines."""

from dataclasses import dataclass, field
import re
from typing import Literal

HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')

LineKind = Literal['meta', 'hunk', 'context', 'added', 'removed', 'note']


@dataclass
class DiffLine:
    kind: LineKind
    text: str
    old: int | None = None
    """Line number in the old file (context and removed lines)."""
    new: int | None = None
    """Line number in the new file (context and added lines)."""


@dataclass
class FileDiff:
    old_path: str | None = None
    new_path: str | None = None
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def paths(self) -> set[str]:
        return {p for p in (self.old_path, self.new_path) if p}

    def line_index(self, line_to: int | None, line_from: int | None) -> int | None:
        """Index of the line a comment is anchored to: `line_to` is a new-file line number,
        `line_from` an old-file one (used for comments on removed lines)."""
        for index, line in enumerate(self.lines):
            if line_to is not None:
                if line.new == line_to and line.kind in ('added', 'context'):
                    return index
            elif line_from is not None:
                if line.old == line_from and line.kind in ('removed', 'context'):
                    return index
        return None


def _header_path(value: str, prefix: str) -> str | None:
    value = value.split('\t', 1)[0]
    return None if value == '/dev/null' else value.removeprefix(prefix)


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    in_hunk = False
    old = new = 0
    for raw in text.rstrip('\n').split('\n'):
        if raw.startswith('diff --git '):
            current = FileDiff()
            files.append(current)
            in_hunk = False
            a, sep, b = raw.removeprefix('diff --git ').rpartition(' b/')
            if sep:
                current.old_path, current.new_path = a.removeprefix('a/'), b
            continue
        if current is None:
            continue
        if match := HUNK_HEADER.match(raw):
            old, new = int(match[1]), int(match[2])
            in_hunk = True
            current.lines.append(DiffLine('hunk', raw))
            continue
        if not in_hunk:
            # File header. `---`/`+++` only count here: inside a hunk they are content lines.
            if raw.startswith('--- '):
                current.old_path = _header_path(raw[4:], 'a/')
            elif raw.startswith('+++ '):
                current.new_path = _header_path(raw[4:], 'b/')
            elif raw.startswith('rename from '):
                current.old_path = raw.removeprefix('rename from ')
            elif raw.startswith('rename to '):
                current.new_path = raw.removeprefix('rename to ')
            elif raw.startswith('new file mode'):
                current.old_path = None
            elif raw.startswith('deleted file mode'):
                current.new_path = None
            if raw and not raw.startswith(('index ', '--- ', '+++ ')):
                current.lines.append(DiffLine('meta', raw))
            continue
        if raw.startswith('+'):
            current.lines.append(DiffLine('added', raw, new=new))
            new += 1
        elif raw.startswith('-'):
            current.lines.append(DiffLine('removed', raw, old=old))
            old += 1
        elif raw.startswith('\\'):
            current.lines.append(DiffLine('note', raw))
        else:
            current.lines.append(DiffLine('context', raw, old=old, new=new))
            old += 1
            new += 1
    return files
