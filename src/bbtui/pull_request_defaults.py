"""Default title and description for a new pull request, following Bitbucket's web UI: one
commit gives its summary as the title, several give a title made from the branch name; the
description lists the commit summaries."""

import re

from bbtui.models import Commit

ISSUE_KEY = re.compile(r'^([A-Z][A-Z0-9]+-\d+)[-_ ]*(.*)$')


def title_from_branch(branch: str) -> str:
    """`feature/ABC-4281_widget_pause-resume` → `ABC-4281 Widget pause resume`."""
    name = branch.rsplit('/', 1)[-1]
    key, rest = '', name
    if match := ISSUE_KEY.match(name):
        key, rest = match[1], match[2]
    words = re.sub(r'[-_]+', ' ', rest).strip()
    words = words[:1].upper() + words[1:]
    return ' '.join(part for part in (key, words) if part)


def default_title(branch: str, commits: list[Commit]) -> str:
    """`commits` is newest first, as the API returns them."""
    if len(commits) == 1 and commits[0].summary:
        return commits[0].summary
    return title_from_branch(branch)


def default_description(commits: list[Commit]) -> str:
    return '\n'.join(f'* {c.summary}' for c in reversed(commits) if c.summary)
