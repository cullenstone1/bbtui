from bbtui.merge import build_check, merge_checks, verdict
from bbtui.models import BuildStatus, DiffStat, PullRequest
from tests.factories import diffstat_json, pull_request_json, user_json


def status(state: str) -> BuildStatus:
    return BuildStatus(key=state, name=state, state=state)


def approved_pr(**overrides) -> PullRequest:
    participants = [
        {'user': user_json('Bob'), 'role': 'REVIEWER', 'approved': True, 'state': 'approved'}
    ]
    return PullRequest.from_api(pull_request_json(1, participants=participants, **overrides))


CLEAN = [DiffStat.from_api(diffstat_json('modified', 'a.py', 'a.py'))]


def test_ready_when_nothing_blocks():
    checks = merge_checks(approved_pr(), CLEAN, [status('SUCCESSFUL')], 0)
    assert verdict(checks) == ('ok', 'No blockers found')
    assert [c.label for c in checks] == [
        'No merge conflicts',
        '1 approval',
        'All 1 build passed',
        'No open tasks',
    ]


def test_blockers():
    conflicted = CLEAN + [DiffStat.from_api(diffstat_json('merge conflict', 'b.py', 'b.py'))]
    pr = PullRequest.from_api(pull_request_json(1, draft=True))  # Cy requested changes
    checks = merge_checks(pr, conflicted, [status('FAILED')], 2)
    labels = {c.label: c.state for c in checks}
    assert labels['Draft'] == 'blocked'
    assert labels['Merge conflicts in 1 file'] == 'blocked'
    assert labels['Changes requested by Cy'] == 'blocked'
    assert labels['2 open tasks'] == 'blocked'
    assert verdict(checks)[0] == 'blocked'


def test_pending_without_approvals_or_while_building():
    pr = PullRequest.from_api(pull_request_json(1, participants=[]))
    checks = merge_checks(pr, CLEAN, [status('INPROGRESS')], 0)
    assert verdict(checks) == ('pending', 'Waiting')


def test_unknown_builds_and_tasks_are_left_out():
    checks = merge_checks(approved_pr(), CLEAN, None, None)
    assert [c.label for c in checks] == ['No merge conflicts', '1 approval']


def test_build_check_counts():
    assert build_check([]).label == 'No builds'
    assert build_check([status('SUCCESSFUL'), status('STOPPED')]).label == '1 of 2 builds failed'
    assert build_check([status('INPROGRESS'), status('SUCCESSFUL')]).state == 'pending'
