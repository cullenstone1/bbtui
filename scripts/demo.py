"""Run bbtui against a made-up Bitbucket workspace, for demos and screenshots.

    python scripts/demo.py

Nothing here talks to Bitbucket or reads your settings: the real app runs with its HTTP client
answered by `DemoBitbucket` (repositories, pull requests, diffs, comments and pipelines for an
invented `acme` workspace), your config file and `BBTUI_*` variables are ignored, and the working
directory is an empty temporary one so nothing is read from your git checkout. Timestamps are
relative to now, so "updated 2 hours ago" stays true whenever you record.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
import difflib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import httpx

from bbtui.api import BitbucketAPI, BitbucketClient
from bbtui.config import Settings

WORKSPACE = 'acme'
BASE_URL = 'https://bitbucket.demo.invalid/2.0'
"""Never resolvable, so a request that somehow bypassed the fake would fail, not leak."""
STARRED = ['payments-api', 'web-app', 'infra']
NOW = datetime.now(UTC).replace(microsecond=0)


def ago(**delta: float) -> str:
    return (NOW - timedelta(**delta)).isoformat()


# --- people -----------------------------------------------------------------------------------


def user(name: str) -> dict:
    nickname = name.split()[0].lower()
    return {
        'display_name': name,
        'nickname': nickname,
        'account_id': f'demo-{nickname}',
        'uuid': f'{{demo-{nickname}}}',
        'type': 'user',
    }


ME = user('Sam Rivera')
ADA = user('Ada Okafor')
BEN = user('Ben Lindqvist')
CHLOE = user('Chloe Martin')
DEV = user('Dev Patel')


def reviewer(person: dict, state: str | None = None) -> dict:
    return {
        'user': person,
        'role': 'REVIEWER',
        'approved': state == 'approved',
        'state': state,
    }


# --- repositories -------------------------------------------------------------------------------

REPOSITORIES = {
    'payments-api': ('Payments, refunds and webhooks', 'main', {'hours': 2}),
    'web-app': ('The customer web app', 'develop', {'hours': 5}),
    'infra': ('Terraform and deployment config', 'main', {'days': 1}),
    'mobile-app': ('iOS and Android app', 'develop', {'hours': 1}),
    'auth-service': ('Sign-in, sessions and SSO', 'main', {'hours': 9}),
    'data-pipeline': ('Nightly exports and reporting jobs', 'main', {'days': 2}),
    'design-system': ('Shared UI components', 'main', {'days': 3}),
    'docs-site': ('Public developer docs', 'main', {'days': 6}),
}


def repository(slug: str) -> dict:
    description, main_branch, updated = REPOSITORIES[slug]
    return {
        'type': 'repository',
        'full_name': f'{WORKSPACE}/{slug}',
        'name': slug,
        'slug': slug,
        'description': description,
        'is_private': True,
        'updated_on': ago(**updated),
        'mainbranch': {'name': main_branch},
        'links': {'html': {'href': f'https://bitbucket.org/{WORKSPACE}/{slug}'}},
    }


# --- diffs --------------------------------------------------------------------------------------

DELIVERY_BEFORE = '''\
"""Delivering webhook events to merchant endpoints."""

import httpx

from payments.webhooks.models import Delivery, Event

TIMEOUT_SECONDS = 10


def deliver(event: Event, endpoint: str) -> Delivery:
    """POST the event to the merchant's endpoint once."""
    response = httpx.post(endpoint, json=event.payload(), timeout=TIMEOUT_SECONDS)
    return Delivery(event_id=event.id, status=response.status_code)


def is_success(delivery: Delivery) -> bool:
    return 200 <= delivery.status < 300
'''

DELIVERY_AFTER = '''\
"""Delivering webhook events to merchant endpoints."""

import time

import httpx

from payments.webhooks.backoff import delays
from payments.webhooks.models import Delivery, Event

TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 6


def deliver(event: Event, endpoint: str) -> Delivery:
    """POST the event to the merchant's endpoint, retrying failures with backoff."""
    for attempt, delay in enumerate(delays(MAX_ATTEMPTS), start=1):
        try:
            response = httpx.post(endpoint, json=event.payload(), timeout=TIMEOUT_SECONDS)
        except httpx.TransportError:
            time.sleep(delay)
            continue
        delivery = Delivery(event_id=event.id, status=response.status_code, attempts=attempt)
        if is_success(delivery) or not is_retryable(delivery):
            return delivery
        time.sleep(delay)
    return Delivery(event_id=event.id, status=0, attempts=MAX_ATTEMPTS)


def is_success(delivery: Delivery) -> bool:
    return 200 <= delivery.status < 300


def is_retryable(delivery: Delivery) -> bool:
    """Server errors and rate limits are worth another try; other client errors aren't."""
    return delivery.status >= 500 or delivery.status == 429
'''

BACKOFF_AFTER = '''\
"""Retry delays: exponential, capped, with jitter so retries don't arrive in lockstep."""

from collections.abc import Iterator
import random

BASE_SECONDS = 1.0
CAP_SECONDS = 300.0


def delays(attempts: int) -> Iterator[float]:
    for attempt in range(attempts):
        yield random.uniform(0, min(CAP_SECONDS, BASE_SECONDS * 2**attempt))
'''

TEST_BEFORE = """\
from payments.webhooks.delivery import deliver, is_success


def test_success(fake_endpoint, event):
    fake_endpoint.respond(200)
    assert is_success(deliver(event, fake_endpoint.url))
"""

TEST_AFTER = """\
from payments.webhooks.delivery import MAX_ATTEMPTS, deliver, is_success


def test_success(fake_endpoint, event):
    fake_endpoint.respond(200)
    assert is_success(deliver(event, fake_endpoint.url))


def test_retries_server_errors(fake_endpoint, event, no_sleep):
    fake_endpoint.respond(503, 503, 200)
    delivery = deliver(event, fake_endpoint.url)
    assert is_success(delivery)
    assert delivery.attempts == 3


def test_gives_up_after_max_attempts(fake_endpoint, event, no_sleep):
    fake_endpoint.respond(*[500] * MAX_ATTEMPTS)
    assert deliver(event, fake_endpoint.url).attempts == MAX_ATTEMPTS


def test_does_not_retry_client_errors(fake_endpoint, event, no_sleep):
    fake_endpoint.respond(400)
    assert deliver(event, fake_endpoint.url).attempts == 1
"""

WEBHOOK_FILES = [  # (path, before, after); None for a file that doesn't exist on that side.
    ('payments/webhooks/delivery.py', DELIVERY_BEFORE, DELIVERY_AFTER),
    ('payments/webhooks/backoff.py', None, BACKOFF_AFTER),
    ('tests/webhooks/test_delivery.py', TEST_BEFORE, TEST_AFTER),
]


def file_diff(path: str, before: str | None, after: str | None) -> str:
    """A `git diff` section for one file, with real hunks."""
    old = (before or '').splitlines(keepends=True)
    new = (after or '').splitlines(keepends=True)
    hunks = list(difflib.unified_diff(old, new, n=3))[2:]  # Our own ---/+++ lines below.
    header = f'diff --git a/{path} b/{path}\n'
    if before is None:
        header += 'new file mode 100644\n--- /dev/null\n'
    else:
        header += f'--- a/{path}\n'
    header += '+++ /dev/null\n' if after is None else f'+++ b/{path}\n'
    return header + ''.join(hunks)


def diff_and_stat(files: Iterable[tuple[str, str | None, str | None]]) -> tuple[str, list[dict]]:
    diff, stat = '', []
    for path, before, after in files:
        section = file_diff(path, before, after)
        lines = section.splitlines()
        stat.append(
            {
                'status': 'added' if before is None else 'removed' if after is None else 'modified',
                'old': None if before is None else {'path': path},
                'new': None if after is None else {'path': path},
                'lines_added': sum(
                    1 for line in lines if line.startswith('+') and line[:3] != '+++'
                ),
                'lines_removed': sum(
                    1 for line in lines if line.startswith('-') and line[:3] != '---'
                ),
            }
        )
        diff += section
    return diff, stat


def line_of(text: str, needle: str) -> int:
    """The 1-based line number of the first line containing `needle`."""
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


# --- pull requests ------------------------------------------------------------------------------


def pull_request(
    slug: str,
    pr_id: int,
    title: str,
    author: dict,
    source: str,
    updated: dict,
    participants: list[dict],
    *,
    destination: str | None = None,
    description: str = '',
    comments: int = 0,
    draft: bool = False,
    state: str = 'OPEN',
    created: dict | None = None,
) -> dict:
    return {
        'type': 'pullrequest',
        'id': pr_id,
        'title': title,
        'description': description,
        'state': state,
        'draft': draft,
        'author': author,
        'source': {'branch': {'name': source}, 'commit': {'hash': f'{pr_id:04x}c0ffee1234'}},
        'destination': {
            'branch': {'name': destination or REPOSITORIES[slug][1]},
            'repository': {'full_name': f'{WORKSPACE}/{slug}'},
        },
        'created_on': ago(**(created or {'days': 2})),
        'updated_on': ago(**updated),
        'comment_count': comments,
        'task_count': 0,
        'close_source_branch': True,
        'participants': participants,
        'links': {
            'html': {'href': f'https://bitbucket.org/{WORKSPACE}/{slug}/pull-requests/{pr_id}'}
        },
    }


WEBHOOK_DESCRIPTION = """\
Merchants lose events when their endpoint has a blip: we try **once** and give up.

This retries deliveries that fail with a server error, a rate limit or a dropped connection,
up to six attempts with capped exponential backoff and jitter.

- Client errors (other 4xx) are still not retried; they won't fix themselves.
- `Delivery.attempts` records how many tries it took, for the delivery log.

Closes PAY-1187.
"""


def make_pull_requests() -> dict[tuple[str, int], dict]:
    prs = [
        # Waiting for my review.
        pull_request(
            'payments-api',
            142,
            'Retry failed webhook deliveries with backoff',
            ADA,
            'feature/PAY-1187-webhook-retries',
            {'minutes': 40},
            [reviewer(ME), reviewer(CHLOE, 'approved'), reviewer(DEV)],
            description=WEBHOOK_DESCRIPTION,
            comments=4,
            created={'hours': 20, 'minutes': 10},
        ),
        pull_request(
            'web-app',
            388,
            'Dark mode for the settings page',
            BEN,
            'feature/settings-dark-mode',
            {'hours': 3},
            [reviewer(ME), reviewer(ADA, 'approved'), reviewer(CHLOE, 'approved')],
            comments=7,
        ),
        pull_request(
            'mobile-app',
            1204,
            'Cache product images on device',
            CHLOE,
            'feature/image-cache',
            {'hours': 6},
            [reviewer(ME), reviewer(BEN, 'changes_requested')],
            comments=2,
        ),
        # Reviewed and approved already, so not waiting.
        pull_request(
            'infra',
            57,
            'Upgrade Postgres to 16',
            DEV,
            'chore/postgres-16',
            {'days': 1},
            [reviewer(ME, 'approved'), reviewer(ADA)],
            comments=1,
        ),
        # Mine.
        pull_request(
            'payments-api',
            145,
            'Idempotency keys for refunds',
            ME,
            'feature/PAY-1201-refund-idempotency',
            {'hours': 1},
            [reviewer(ADA, 'approved'), reviewer(DEV, 'approved')],
            comments=3,
        ),
        pull_request(
            'web-app',
            391,
            'Fix the date picker in Safari',
            ME,
            'fix/safari-date-picker',
            {'hours': 2},
            [reviewer(BEN), reviewer(CHLOE, 'changes_requested')],
            comments=5,
        ),
        pull_request(
            'auth-service',
            77,
            'Rotate session signing keys',
            ME,
            'feature/key-rotation',
            {'minutes': 20},
            [reviewer(DEV)],
            draft=True,
        ),
        # The rest of payments-api's open pull requests.
        pull_request(
            'payments-api',
            139,
            'Bump httpx to 0.28',
            DEV,
            'chore/bump-httpx',
            {'days': 2},
            [reviewer(ADA, 'approved'), reviewer(CHLOE)],
        ),
        pull_request(
            'payments-api',
            144,
            'Show refund reasons in the dashboard export',
            CHLOE,
            'feature/refund-reasons-export',
            {'hours': 4},
            [reviewer(ADA), reviewer(DEV)],
            comments=1,
        ),
    ]
    return {
        (pr['destination']['repository']['full_name'].split('/')[1], pr['id']): pr for pr in prs
    }


def comment(
    comment_id: int,
    author: dict,
    body: str,
    created: dict,
    *,
    parent: int | None = None,
    inline: dict | None = None,
) -> dict:
    data = {
        'id': comment_id,
        'user': author,
        'content': {'raw': body},
        'created_on': ago(**created),
        'deleted': False,
    }
    if parent is not None:
        data['parent'] = {'id': parent}
    if inline is not None:
        data['inline'] = inline
    return data


def webhook_comments() -> list[dict]:
    delivery = 'payments/webhooks/delivery.py'
    retry_line = line_of(DELIVERY_AFTER, 'return delivery.status >= 500')
    sleep_line = line_of(DELIVERY_AFTER, 'except httpx.TransportError')
    return [
        comment(
            1,
            CHLOE,
            'Nice, this has bitten us twice this quarter. Approved; one thought inline.',
            {'minutes': 55},
        ),
        comment(
            2,
            CHLOE,
            'Should `408 Request Timeout` count as retryable too? Some merchants sit behind '
            'proxies that send it.',
            {'minutes': 54},
            inline={'path': delivery, 'to': retry_line, 'from': None},
        ),
        comment(
            3,
            ADA,
            "Good call, I'll add it in a follow-up with the delivery-log change.",
            {'minutes': 45},
            parent=2,
        ),
        comment(
            4,
            DEV,
            'Worth logging the exception here, so we can tell timeouts from refused connections.',
            {'minutes': 41},
            inline={'path': delivery, 'to': sleep_line, 'from': None},
        ),
    ]


def update(author: dict, created: dict, commit: str, **fields) -> dict:
    return {
        'update': {
            'author': author,
            'date': ago(**created),
            'source': {'commit': {'hash': commit}},
            'draft': False,
            'changes': {},
            **fields,
        }
    }


WEBHOOK_ACTIVITY = [
    {'approval': {'user': CHLOE, 'date': ago(minutes=55)}},
    update(ADA, {'minutes': 40}, 'c3a1'),
    update(ADA, {'hours': 3}, 'b2f9'),
    update(ADA, {'hours': 20}, 'a17e', changes={'draft': {'old': True, 'new': False}}),
    update(
        ADA,
        {'hours': 20, 'minutes': 5},
        'a17e',
        changes={'reviewers': {'added': [ME, CHLOE, DEV]}},
    ),
    update(ADA, {'hours': 20, 'minutes': 10}, 'a17e', draft=True),
]


def build_status(state: str, name: str, number: int, slug: str, updated: dict) -> dict:
    return {
        'key': f'pipeline-{number}',
        'name': name,
        'state': state,
        'url': f'https://bitbucket.org/{WORKSPACE}/{slug}/pipelines/results/{number}',
        'updated_on': ago(**updated),
    }


BUILD_STATUSES = {
    ('payments-api', 142): [
        build_status(
            'SUCCESSFUL',
            'Pipeline #2214 for feature/PAY-1187',
            2214,
            'payments-api',
            {'minutes': 30},
        )
    ],
    ('payments-api', 145): [
        build_status(
            'SUCCESSFUL',
            'Pipeline #2216 for feature/PAY-1201',
            2216,
            'payments-api',
            {'minutes': 50},
        )
    ],
    ('web-app', 388): [
        build_status(
            'SUCCESSFUL',
            'Pipeline #981 for feature/settings-dark-mode',
            981,
            'web-app',
            {'hours': 3},
        )
    ],
    ('web-app', 391): [
        build_status(
            'FAILED', 'Pipeline #983 for fix/safari-date-picker', 983, 'web-app', {'hours': 2}
        )
    ],
    ('mobile-app', 1204): [
        build_status(
            'INPROGRESS',
            'Pipeline #4410 for feature/image-cache',
            4410,
            'mobile-app',
            {'minutes': 3},
        )
    ],
}


# --- pipelines ----------------------------------------------------------------------------------


def pipeline(
    number: int,
    result: str,
    created: dict,
    seconds: int,
    *,
    creator: dict = ME,
    branch: str | None = None,
    pr: tuple[str, str] | None = None,
    schedule: str | None = None,
) -> dict:
    if result == 'RUNNING':
        state = {'name': 'IN_PROGRESS', 'stage': {'name': 'RUNNING'}}
    else:
        state = {'name': 'COMPLETED', 'result': {'name': result}}
    if pr:
        target = {
            'type': 'pipeline_pullrequest_target',
            'selector': {'type': 'pull-requests', 'pattern': '**'},
            'source': pr[0],
            'destination': pr[1],
        }
    elif schedule:
        target = {
            'type': 'pipeline_ref_target',
            'ref_name': branch,
            'selector': {'type': 'custom', 'pattern': schedule},
        }
    else:
        target = {
            'type': 'pipeline_ref_target',
            'ref_name': branch,
            'selector': {'type': 'branches', 'pattern': branch},
        }
    target['commit'] = {'hash': f'{number:x}deadbeef0123'}
    return {
        'uuid': f'{{run-{number}}}',
        'build_number': number,
        'state': state,
        'trigger': {'name': 'SCHEDULE' if schedule else 'PUSH'},
        'creator': creator,
        'created_on': ago(**created),
        'completed_on': None if result == 'RUNNING' else ago(**created),
        'duration_in_seconds': seconds,
        'target': target,
    }


def older_runs(newest: int, count: int) -> list[dict]:
    """A day-by-day history of main-branch and nightly runs, mostly passing."""
    people = [ADA, DEV, CHLOE, ME, BEN]
    runs = []
    for index in range(count):
        number = newest - index
        hours = 30 + index * 9
        if index % 4 == 1:
            runs.append(
                pipeline(
                    number,
                    'SUCCESSFUL',
                    {'hours': hours},
                    890 + index * 7,
                    branch='main',
                    schedule='nightly-e2e',
                )
            )
        else:
            result = 'FAILED' if index % 7 == 5 else 'SUCCESSFUL'
            runs.append(
                pipeline(
                    number,
                    result,
                    {'hours': hours},
                    380 + index * 11,
                    creator=people[index % 5],
                    branch='main',
                )
            )
    return runs


PIPELINES = {
    'payments-api': [
        pipeline(
            2216,
            'SUCCESSFUL',
            {'minutes': 55},
            412,
            pr=('feature/PAY-1201-refund-idempotency', 'main'),
        ),
        pipeline(
            2215,
            'FAILED',
            {'hours': 1, 'minutes': 20},
            389,
            pr=('feature/PAY-1201-refund-idempotency', 'main'),
        ),
        pipeline(
            2214,
            'SUCCESSFUL',
            {'hours': 1, 'minutes': 35},
            431,
            creator=ADA,
            pr=('feature/PAY-1187-webhook-retries', 'main'),
        ),
        pipeline(
            2213, 'SUCCESSFUL', {'hours': 7}, 905, creator=ME, branch='main', schedule='nightly-e2e'
        ),
        pipeline(2212, 'SUCCESSFUL', {'hours': 9}, 398, creator=DEV, branch='main'),
        pipeline(2211, 'STOPPED', {'hours': 10}, 61, creator=DEV, pr=('chore/bump-httpx', 'main')),
        pipeline(
            2210,
            'SUCCESSFUL',
            {'days': 1},
            402,
            creator=CHLOE,
            pr=('feature/refund-reasons-export', 'main'),
        ),
        *older_runs(2209, 14),
    ],
    'web-app': [
        pipeline(983, 'FAILED', {'hours': 2}, 287, pr=('fix/safari-date-picker', 'develop')),
        pipeline(
            982, 'SUCCESSFUL', {'hours': 9}, 1310, branch='develop', schedule='nightly-visual-tests'
        ),
        pipeline(
            981,
            'SUCCESSFUL',
            {'hours': 3},
            301,
            creator=BEN,
            pr=('feature/settings-dark-mode', 'develop'),
        ),
    ],
    'infra': [
        pipeline(120, 'FAILED', {'hours': 6}, 640, branch='main', schedule='drift-check'),
        pipeline(119, 'SUCCESSFUL', {'days': 1}, 210, creator=DEV, branch='main'),
    ],
}

SCHEDULES = {
    'payments-api': [('nightly-e2e', 'main')],
    'web-app': [('nightly-visual-tests', 'develop')],
    'infra': [('drift-check', 'main')],
}

STEP_NAMES = ['Lint', 'Unit tests', 'Integration tests', 'Build image']


def steps(run: dict) -> list[dict]:
    result = run['state'].get('result', {}).get('name', 'RUNNING')
    out = []
    for index, name in enumerate(STEP_NAMES):
        if result == 'FAILED' and name == 'Integration tests':
            step_result = 'FAILED'
        elif result == 'FAILED' and index > STEP_NAMES.index('Integration tests'):
            step_result = 'NOT_RUN'
        elif result == 'STOPPED' and index > 0:
            step_result = 'STOPPED'
        else:
            step_result = 'SUCCESSFUL'
        out.append(
            {
                'uuid': f'{{step-{run["build_number"]}-{index}}}',
                'name': name,
                'state': {'name': 'COMPLETED', 'result': {'name': step_result}},
                'started_on': run['created_on'],
                'completed_on': run['completed_on'],
                'duration_in_seconds': [24, 96, 241, 75][index]
                if step_result != 'NOT_RUN'
                else None,
            }
        )
    return out


GREEN, RED, BOLD, DIM, RESET = '\x1b[32m', '\x1b[31m', '\x1b[1m', '\x1b[2m', '\x1b[0m'


def step_log(name: str, failed: bool) -> bytes:
    lines = [
        f'{DIM}+ docker run --rm acme/ci-python:3.13{RESET}',
        f'{BOLD}== {name} =={RESET}',
        '+ uv sync --frozen',
        'Resolved 84 packages in 12ms',
        'Installed 84 packages in 1.41s',
    ]
    if name != 'Integration tests':
        lines += [f'+ make {name.lower().replace(" ", "-")}', f'{GREEN}All checks passed{RESET}']
        return ('\n'.join(lines) + '\n').encode()
    lines += ['+ pytest tests/integration -q', '']
    for module in ('refunds', 'webhooks', 'payouts', 'disputes', 'ledger', 'fx'):
        lines.append(f'tests/integration/test_{module}.py ' + '.' * 18 + f'  [{module}]')
    if failed:
        lines += [
            '',
            f'{RED}{BOLD}=================================== FAILURES ==================================={RESET}',
            '____________________ test_partial_refund_is_idempotent _____________________',
            '',
            '    def test_partial_refund_is_idempotent(client, charge):',
            '        key = "refund-7f3a"',
            '        first = client.refund(charge, amount=500, idempotency_key=key)',
            '        second = client.refund(charge, amount=500, idempotency_key=key)',
            '>       assert second.id == first.id',
            "E       AssertionError: assert 're_9Kx2' == 're_4Tq8'",
            '',
            'tests/integration/test_refunds.py:88: AssertionError',
            f'{RED}FAILED tests/integration/test_refunds.py::test_partial_refund_is_idempotent{RESET}',
            f'{RED}1 failed{RESET}, {GREEN}107 passed{RESET} in 231.08s',
            'make: *** [Makefile:31: integration-tests] Error 1',
        ]
    else:
        lines.append(f'{GREEN}108 passed{RESET} in 228.40s')
    return ('\n'.join(lines) + '\n').encode()


# --- commits and refs ---------------------------------------------------------------------------

COMMIT_MESSAGES = [
    (ADA, 'Retry webhook deliveries with capped exponential backoff'),
    (DEV, 'Bump httpx to 0.28.1'),
    (ME, 'Store idempotency keys for refunds for 24 hours'),
    (CHLOE, 'Add refund reasons to the dashboard export'),
    (ADA, 'Log the merchant endpoint when a delivery fails'),
    (DEV, 'Pin the Postgres image used in integration tests'),
    (ME, 'Reject refunds larger than the remaining charge amount'),
    (BEN, 'Document the webhook signature header'),
    (CHLOE, 'Split payouts into their own module'),
    (ME, 'Release 3.14.0'),
    (ADA, 'Handle currency rounding for JPY and KRW'),
    (DEV, 'Run integration tests against Postgres 16'),
]


def commits() -> list[dict]:
    out = []
    for index, (author, message) in enumerate(COMMIT_MESSAGES):
        commit_hash = f'{index * 7919 + 4099:08x}' + 'ab12cd34ef56' * 2 + 'beef'
        out.append(
            {
                'hash': commit_hash[:40],
                'message': message + '\n',
                'author': {
                    'raw': f'{author["display_name"]} <{author["nickname"]}@acme.example>',
                    'user': author,
                },
                'date': ago(hours=3 + index * 7),
                'parents': [],
            }
        )
    for current, parent in zip(out, out[1:], strict=False):
        current['parents'] = [{'hash': parent['hash']}]
    return out


def refs(slug: str) -> list[dict]:
    main_branch = REPOSITORIES[slug][1]
    names = [
        main_branch,
        'feature/PAY-1187-webhook-retries',
        'feature/PAY-1201-refund-idempotency',
        'chore/bump-httpx',
    ]
    branches = [
        {
            'type': 'branch',
            'name': name,
            'target': {'hash': f'{i:040x}', 'date': ago(hours=1 + i * 5)},
        }
        for i, name in enumerate(names)
    ]
    tags = [
        {
            'type': 'tag',
            'name': f'v3.{14 - i}.0',
            'target': {
                'hash': COMMITS[9 + i]['hash'],  # Releases among the older commits.
                'date': ago(days=3 + i * 9),
            },
        }
        for i in range(3)
    ]
    return branches + tags


COMMITS = commits()


# --- the fake server ----------------------------------------------------------------------------


class DemoBitbucket:
    """Answers the Bitbucket REST requests bbtui makes, from the data above.

    Reviews, comments, draft changes and merges are kept in memory, so the demo responds to
    them; `unhandled` lists any request it had no answer for.
    """

    def __init__(self) -> None:
        self.pull_requests = make_pull_requests()
        self.comments = {('payments-api', 142): webhook_comments()}
        self.diff, self.diffstat = diff_and_stat(WEBHOOK_FILES)
        self.unhandled: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix('/2.0')
        try:
            result = self.route(request, path)
        except KeyError:
            result = None
        if result is None:
            self.unhandled.append(f'{request.method} {path}')
            return httpx.Response(404, json={'error': {'message': 'Not in the demo data'}})
        if isinstance(result, httpx.Response):
            return result
        if isinstance(result, (str, bytes)):
            return httpx.Response(200, content=result, headers={'content-type': 'text/plain'})
        if isinstance(result, list):
            result = {'values': result, 'pagelen': len(result), 'size': len(result)}
        return httpx.Response(200, json=result)

    def route(self, request: httpx.Request, path: str):  # noqa: C901, PLR0911, PLR0912
        method, params = request.method, request.url.params
        if path == '/user':
            return ME
        if path == '/user/workspaces':
            return [{'administrator': False, 'workspace': {'slug': WORKSPACE, 'name': 'Acme'}}]
        if path == f'/workspaces/{WORKSPACE}/pullrequests/{ME["uuid"]}':
            mine = [pr for pr in self.pull_requests.values() if pr['author'] is ME]
            return self.sorted_open(mine, params)
        if path == f'/repositories/{WORKSPACE}':
            repos = [repository(slug) for slug in REPOSITORIES]
            if match := re.search(r'name ~ "(.*)"', params.get('q', '')):
                repos = [r for r in repos if match[1].lower() in r['slug']]
            return sorted(repos, key=lambda r: r['updated_on'], reverse=True)

        match = re.fullmatch(rf'/repositories/{WORKSPACE}/([^/]+)(/.*)?', path)
        if not match or match[1] not in REPOSITORIES:
            return None
        slug, rest = match[1], match[2] or ''
        if rest == '':
            return repository(slug)
        if rest == '/pullrequests' and method == 'GET':
            prs = [pr for (s, _), pr in self.pull_requests.items() if s == slug]
            query = params.get('q', '')
            if reviewer_uuid := re.search(r'reviewers\.uuid = "([^"]+)"', query):
                prs = [
                    pr
                    for pr in prs
                    if any(p['user']['uuid'] == reviewer_uuid[1] for p in pr['participants'])
                ]
            if source := re.search(r'source\.branch\.name = "([^"]+)"', query):
                prs = [pr for pr in prs if pr['source']['branch']['name'] == source[1]]
            return self.sorted_open(prs, params, default_state='OPEN' if 'state' in query else '')
        if pr_match := re.fullmatch(r'/pullrequests/(\d+)(/.*)?', rest):
            return self.pull_request_route(request, slug, int(pr_match[1]), pr_match[2] or '')
        if rest == '/pipelines':
            return sorted(PIPELINES.get(slug, []), key=lambda r: r['created_on'], reverse=True)
        if run_match := re.fullmatch(r'/pipelines/([^/]+)(/.*)?', rest):
            return self.pipeline_route(slug, run_match[1], run_match[2] or '')
        if rest == '/pipelines_config/schedules':
            return [
                {
                    'uuid': f'{{schedule-{name}}}',
                    'enabled': True,
                    'cron_pattern': '0 0 3 * * ? *',
                    'target': {'ref_name': ref, 'selector': {'type': 'custom', 'pattern': name}},
                }
                for name, ref in SCHEDULES.get(slug, [])
            ]
        if rest == '/branching-model':
            return {'development': {'name': REPOSITORIES[slug][1]}}
        if rest == '/effective-default-reviewers':
            return [{'user': person} for person in (ADA, DEV)]
        if rest in ('/refs', '/refs/branches', '/refs/tags'):
            kind = {'/refs': None, '/refs/branches': 'branch', '/refs/tags': 'tag'}[rest]
            found = [ref for ref in refs(slug) if kind in (None, ref['type'])]
            if match := re.search(r'name ~ "(.*)"', params.get('q', '')):
                found = [ref for ref in found if match[1].lower() in ref['name'].lower()]
            return found
        if branch := re.fullmatch(r'/refs/branches/(.+)', rest):
            if branch[1] not in [ref['name'] for ref in refs(slug)]:
                return httpx.Response(404, json={'error': {'message': 'No such branch'}})
            return {
                'name': branch[1],
                'merge_strategies': ['merge_commit', 'squash', 'fast_forward'],
                'default_merge_strategy': 'squash',
            }
        if rest == '/commits':
            return COMMITS
        if re.fullmatch(r'/diffstat/[^/]+', rest):
            return self.diffstat
        if re.fullmatch(r'/diff/[^/]+', rest):
            return self.diff
        return None

    def sorted_open(self, prs: list[dict], params, default_state: str = 'OPEN') -> list[dict]:
        state = params.get('state', default_state)
        if state:
            prs = [pr for pr in prs if pr['state'] == state]
        return sorted(prs, key=lambda pr: pr['updated_on'], reverse=True)

    def pull_request_route(self, request: httpx.Request, slug: str, pr_id: int, rest: str):  # noqa: PLR0911
        pr = self.pull_requests[(slug, pr_id)]
        method = request.method
        if rest == '':
            if method == 'PUT':
                pr['draft'] = json.loads(request.content).get('draft', pr['draft'])
            return pr
        if rest == '/diffstat':
            return self.diffstat
        if rest == '/diff':
            return self.diff
        if rest == '/comments':
            comments = self.comments.setdefault((slug, pr_id), [])
            if method == 'POST':
                body = json.loads(request.content)
                parent = (body.get('parent') or {}).get('id')
                inline = body.get('inline')
                if parent is not None:
                    inline = next((c.get('inline') for c in comments if c['id'] == parent), None)
                new = comment(
                    100 + len(comments),
                    ME,
                    body['content']['raw'],
                    {'seconds': 1},
                    parent=parent,
                    inline=inline,
                )
                comments.append(new)
                pr['comment_count'] += 1
                return new
            return comments
        if rest == '/activity':
            return (
                WEBHOOK_ACTIVITY
                if (slug, pr_id) == ('payments-api', 142)
                else [update(pr['author'], {'days': 1}, 'aaaa')]
            )
        if rest == '/statuses':
            return BUILD_STATUSES.get((slug, pr_id), [])
        if rest == '/tasks':
            return []
        if rest in ('/approve', '/request-changes'):
            self.review(
                pr, 'approved' if rest == '/approve' else 'changes_requested', method == 'POST'
            )
            return httpx.Response(204)
        if rest == '/merge':
            pr['state'] = 'MERGED'
            pr['merge_commit'] = {'hash': 'f00dfacecafe1234'}
            return pr
        return None

    def review(self, pr: dict, state: str, on: bool) -> None:
        mine = next((p for p in pr['participants'] if p['user'] is ME), None)
        if mine is None:
            mine = {'user': ME, 'role': 'PARTICIPANT', 'approved': False, 'state': None}
            pr['participants'].append(mine)
        mine['state'] = state if on else None
        mine['approved'] = on and state == 'approved'

    def pipeline_route(self, slug: str, run_id: str, rest: str):
        runs = PIPELINES.get(slug, [])
        run = next(r for r in runs if run_id in (str(r['build_number']), r['uuid']))
        if rest == '':
            return run
        if rest == '/steps':
            return steps(run)
        if step := re.fullmatch(r'/steps/([^/]+)/log', rest):
            found = next(s for s in steps(run) if s['uuid'] == step[1])
            if found['state']['result']['name'] == 'NOT_RUN':
                return httpx.Response(404, json={'error': {'message': 'No log'}})
            return step_log(found['name'], found['state']['result']['name'] == 'FAILED')
        if rest == '/stopPipeline':
            return httpx.Response(204)
        return None


# --- running it ---------------------------------------------------------------------------------


def isolate() -> None:
    """Ignore your config file, `BBTUI_*` variables and git checkout."""
    for name in list(os.environ):
        if name.startswith('BBTUI_'):
            del os.environ[name]
    scratch = Path(tempfile.mkdtemp(prefix='bbtui-demo-'))
    os.environ['BBTUI_CONFIG_FILE'] = str(scratch / 'no-config.yaml')
    os.chdir(scratch)


def demo_settings() -> Settings:
    return Settings(
        username='demo@acme.example',
        api_token='demo',
        workspace=WORKSPACE,
        starred_repos=STARRED,
        recent_repos_limit=8,
        base_url=BASE_URL,
    )


def demo_app(server: DemoBitbucket | None = None):
    """The real bbtui app, wired to the demo server. Call `isolate()` first."""
    from bbtui.app import BBTUI

    server = server or DemoBitbucket()
    settings = demo_settings()
    client = BitbucketClient('demo', 'demo', BASE_URL, server.transport())
    return BBTUI(settings, BitbucketAPI(client))


def main() -> None:
    isolate()
    from bbtui.terminal import apply_colour_override

    # As in `bbtui`, before Textual is imported.
    apply_colour_override()
    server = DemoBitbucket()
    demo_app(server).run()
    if server.unhandled:
        sys.stderr.write('Requests the demo had no data for:\n')
        sys.stderr.writelines(f'  {line}\n' for line in dict.fromkeys(server.unhandled))


if __name__ == '__main__':
    main()
