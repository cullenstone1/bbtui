import json

import httpx
import pytest

from bbtui.api import (
    AuthenticationError,
    BitbucketAPI,
    BitbucketClient,
    NotFoundError,
    PermissionDeniedError,
)
from tests.factories import repository_json

BASE = 'https://api.example/2.0'


def make_api(handler) -> BitbucketAPI:
    return BitbucketAPI(BitbucketClient('me', 'token', BASE, httpx.MockTransport(handler)))


async def test_get_all_follows_next_links():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = request.url.params.get('page', '1')
        if page == '1':
            return httpx.Response(
                200,
                json={'values': [{'id': 1}, {'id': 2}], 'next': f'{BASE}/things?page=2&pagelen=2'},
            )
        return httpx.Response(200, json={'values': [{'id': 3}]})

    client = BitbucketClient('me', 'token', BASE, httpx.MockTransport(handler))
    values = await client.get_all('/things', {'pagelen': 2})
    assert [v['id'] for v in values] == [1, 2, 3]
    assert requests[0].headers['Authorization'].startswith('Basic ')
    assert requests[1].url.params['page'] == '2'


async def test_get_all_stops_at_limit():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={'values': [{'id': 1}, {'id': 2}], 'next': f'{BASE}/x?p=2'})

    client = BitbucketClient('me', 'token', BASE, httpx.MockTransport(handler))
    assert len(await client.get_all('/x', limit=2)) == 2
    assert calls == 1


@pytest.mark.parametrize(
    ('status', 'error'),
    [(401, AuthenticationError), (403, PermissionDeniedError), (404, NotFoundError)],
)
async def test_errors_map_to_exceptions(status, error):
    api = make_api(lambda request: httpx.Response(status, json={'error': {'message': 'nope'}}))
    with pytest.raises(error):
        await api.current_user()


async def test_permission_error_carries_bitbucket_message():
    api = make_api(
        lambda request: httpx.Response(
            403, json={'error': {'message': 'API Token provided has no Bitbucket scopes.'}}
        )
    )
    with pytest.raises(PermissionDeniedError, match='no Bitbucket scopes'):
        await api.workspaces()


async def test_repositories_keeps_order_and_reports_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        workspace, slug = request.url.path.split('/')[-2:]
        if slug == 'ghost':
            return httpx.Response(404, json={'error': {'message': 'not found'}})
        return httpx.Response(200, json=repository_json(slug, workspace))

    api = make_api(handler)
    found, missing = await api.repositories(['b', 'ghost', 'other/a'], 'acme')
    assert [r.full_name for r in found] == ['acme/b', 'other/a']
    assert missing == ['ghost']


async def test_search_quotes_the_query():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={'values': [repository_json('say-hi')]})

    repos = await make_api(handler).search_repositories('acme', 'say "hi"')
    assert seen['q'] == 'name ~ "say \\"hi\\""'
    assert seen['sort'] == '-updated_on'
    assert repos[0].slug == 'say-hi'


async def test_workspaces_use_user_workspaces_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/2.0/user/workspaces'
        return httpx.Response(
            200, json={'values': [{'administrator': False, 'workspace': {'slug': 'acme'}}]}
        )

    assert [w.slug for w in await make_api(handler).workspaces()] == ['acme']


async def test_pull_requests_request_participants():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={'values': []})

    await make_api(handler).pull_requests('acme', 'widgets')
    assert seen['fields'] == '+values.participants'
    assert seen['state'] == 'OPEN'


async def test_statuses_and_open_tasks():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/statuses'):
            return httpx.Response(
                200,
                json={'values': [{'key': 'k', 'name': 'Pipeline', 'state': 'SUCCESSFUL'}]},
            )
        return httpx.Response(
            200, json={'values': [{'state': 'UNRESOLVED'}, {'state': 'RESOLVED'}]}
        )

    api = make_api(handler)
    statuses = await api.pull_request_statuses('acme', 'widgets', 1)
    assert [(s.name, s.state) for s in statuses] == [('Pipeline', 'SUCCESSFUL')]
    assert await api.pull_request_open_task_count('acme', 'widgets', 1) == 1


async def test_review_actions_hit_the_right_endpoints():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path.removeprefix('/2.0/repositories/acme/w')))
        return httpx.Response(200 if request.method == 'POST' else 204)

    api = make_api(handler)
    await api.approve('acme', 'w', 5)
    await api.unapprove('acme', 'w', 5)
    await api.request_changes('acme', 'w', 5)
    await api.remove_request_changes('acme', 'w', 5)
    assert seen == [
        ('POST', '/pullrequests/5/approve'),
        ('DELETE', '/pullrequests/5/approve'),
        ('POST', '/pullrequests/5/request-changes'),
        ('DELETE', '/pullrequests/5/request-changes'),
    ]


async def test_comment_payloads():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json={'id': 7, 'content': {'raw': 'x'}})

    api = make_api(handler)
    comment = await api.create_comment('acme', 'w', 5, 'general')
    await api.create_comment('acme', 'w', 5, 'on new line', path='a.py', line_to=12)
    await api.create_comment('acme', 'w', 5, 'on removed line', path='a.py', line_from=3)
    await api.create_comment('acme', 'w', 5, 'reply', path='a.py', line_to=12, parent_id=9)
    assert comment.id == 7
    assert bodies == [
        {'content': {'raw': 'general'}},
        {'content': {'raw': 'on new line'}, 'inline': {'path': 'a.py', 'to': 12}},
        {'content': {'raw': 'on removed line'}, 'inline': {'path': 'a.py', 'from': 3}},
        {'content': {'raw': 'reply'}, 'parent': {'id': 9}},
    ]


async def test_create_pull_request_payload():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={'id': 42, 'title': 'T'})

    pr = await make_api(handler).create_pull_request(
        'acme',
        'w',
        title='T',
        source='feature',
        destination='develop',
        description='D',
        reviewer_uuids=['{u1}'],
        close_source_branch=True,
        draft=True,
    )
    assert pr.id == 42
    assert bodies == [
        (
            '/2.0/repositories/acme/w/pullrequests',
            {
                'title': 'T',
                'description': 'D',
                'source': {'branch': {'name': 'feature'}},
                'destination': {'branch': {'name': 'develop'}},
                'reviewers': [{'uuid': '{u1}'}],
                'close_source_branch': True,
                'draft': True,
            },
        )
    ]


async def test_development_branch_falls_back_to_main_branch():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/branching-model'):
            return httpx.Response(403, json={'error': {'message': 'no'}})
        return httpx.Response(200, json=repository_json('w', mainbranch={'name': 'trunk'}))

    assert await make_api(handler).development_branch('acme', 'w') == 'trunk'

    def model(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'development': {'name': 'develop'}})

    assert await make_api(model).development_branch('acme', 'w') == 'develop'


async def test_branch_queries():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith('/refs/branches/gone'):
            return httpx.Response(404, json={'error': {'message': 'nope'}})
        return httpx.Response(200, json={'values': [], 'name': 'x'})

    api = make_api(handler)
    await api.branches('acme', 'w', 'abc')
    await api.commits_between('acme', 'w', 'feature', 'develop')
    assert await api.branch_exists('acme', 'w', 'gone') is False
    assert seen[0][1]['q'] == 'name ~ "abc"'
    assert seen[0][1]['sort'] == '-target.date'
    assert seen[1][1]['include'] == 'feature'
    assert seen[1][1]['exclude'] == 'develop'


async def test_set_draft_and_merge_strategies():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        if request.url.path.endswith('/refs/branches/master'):
            return httpx.Response(
                200,
                json={
                    'merge_strategies': ['merge_commit', 'squash'],
                    'default_merge_strategy': 'squash',
                },
            )
        return httpx.Response(200, json={'id': 5, 'draft': False})

    api = make_api(handler)
    pr = await api.set_draft('acme', 'w', 5, 'Title', draft=False)
    assert not pr.draft
    assert seen[0][:2] == ('PUT', '/2.0/repositories/acme/w/pullrequests/5')
    assert json.loads(seen[0][2]) == {'title': 'Title', 'draft': False}
    assert await api.merge_strategies('acme', 'w', 'master') == (
        ['merge_commit', 'squash'],
        'squash',
    )


async def test_merge_waits_for_background_merges():
    polls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == 'POST':
            assert json.loads(request.content) == {
                'type': 'pullrequest',
                'merge_strategy': 'squash',
                'close_source_branch': True,
                'message': 'msg',
            }
            return httpx.Response(
                202,
                headers={
                    'location': f'{BASE}/repositories/acme/w/pullrequests/5/merge/task-status/t1'
                },
            )
        polls.append(request.url.path)
        if len(polls) < 2:
            return httpx.Response(200, json={'task_status': 'PENDING'})
        return httpx.Response(
            200, json={'task_status': 'SUCCESS', 'merge_result': {'id': 5, 'state': 'MERGED'}}
        )

    pr = await make_api(handler).merge_pull_request(
        'acme', 'w', 5, strategy='squash', message='msg', close_source_branch=True, poll_seconds=0
    )
    assert pr.state == 'MERGED'
    assert len(polls) == 2


async def test_merge_returns_immediately_when_done():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert 'message' not in body  # Fast-forward: no message.
        return httpx.Response(200, json={'id': 5, 'state': 'MERGED'})

    pr = await make_api(handler).merge_pull_request('acme', 'w', 5, strategy='fast_forward')
    assert pr.state == 'MERGED'
