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
