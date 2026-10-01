"""Merge readiness of a pull request, from what the API exposes to non-admins.

The repository's own merge rules (branch restrictions such as "2 approvals required") need
repository admin access to read, so these checks report facts rather than enforce those rules.
"""

from dataclasses import dataclass
from typing import Literal

from bbtui.models import BuildStatus, DiffStat, PullRequest

CheckState = Literal['ok', 'blocked', 'pending']


@dataclass(frozen=True)
class Check:
    state: CheckState
    label: str


def _plural(count: int, word: str) -> str:
    return f'{count} {word}{"" if count == 1 else "s"}'


def build_check(statuses: list[BuildStatus]) -> Check:
    if not statuses:
        return Check('ok', 'No builds')
    failed = [s for s in statuses if s.state in ('FAILED', 'STOPPED')]
    running = [s for s in statuses if s.state == 'INPROGRESS']
    if failed:
        return Check('blocked', f'{len(failed)} of {_plural(len(statuses), "build")} failed')
    if running:
        return Check('pending', f'{len(running)} of {_plural(len(statuses), "build")} running')
    return Check('ok', f'All {_plural(len(statuses), "build")} passed')


def merge_checks(
    pr: PullRequest,
    diffstat: list[DiffStat],
    statuses: list[BuildStatus] | None,
    open_tasks: int | None,
) -> list[Check]:
    """`statuses` and `open_tasks` are None when they couldn't be loaded."""
    checks: list[Check] = []
    if pr.draft:
        checks.append(Check('blocked', 'Draft'))

    conflicts = sum(1 for stat in diffstat if stat.is_conflicted)
    if conflicts:
        checks.append(Check('blocked', f'Merge conflicts in {_plural(conflicts, "file")}'))
    else:
        checks.append(Check('ok', 'No merge conflicts'))

    requested = [p.user.display_name for p in pr.participants if p.state == 'changes_requested']
    if requested:
        checks.append(Check('blocked', f'Changes requested by {", ".join(requested)}'))
    if pr.approvals:
        checks.append(Check('ok', _plural(pr.approvals, 'approval')))
    else:
        checks.append(Check('pending', 'No approvals yet'))

    if statuses is not None:
        checks.append(build_check(statuses))
    if open_tasks is not None:
        if open_tasks:
            checks.append(Check('blocked', _plural(open_tasks, 'open task')))
        else:
            checks.append(Check('ok', 'No open tasks'))
    return checks


def verdict(checks: list[Check]) -> tuple[CheckState, str]:
    states = {check.state for check in checks}
    if 'blocked' in states:
        return 'blocked', 'Not ready to merge'
    if 'pending' in states:
        return 'pending', 'Waiting'
    return 'ok', 'No blockers found'
