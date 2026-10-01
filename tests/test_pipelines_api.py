import json

import httpx

from bbtui.models import Pipeline, Schedule
from tests.factories import pipeline_json
from tests.test_client import make_api


def test_pipeline_model():
    running = Pipeline.from_api(pipeline_json(5, 'RUNNING'))
    assert running.status == 'RUNNING' and running.is_running
    failed = Pipeline.from_api(pipeline_json(6, 'FAILED'))
    assert failed.status == 'FAILED' and not failed.is_running
    assert failed.description == 'PR feature → master'
    assert failed.commit == 'abcdef1234'
    assert 'links' not in failed.target['commit']
    custom = Pipeline.from_api(
        pipeline_json(
            7,
            target={'ref_name': 'master', 'selector': {'type': 'custom', 'pattern': 'nightly'}},
        )
    )
    assert custom.description == 'custom: nightly on master'


async def test_step_log_ranges():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get('range'))
        if request.headers.get('range') == 'bytes=10-':
            return httpx.Response(206, content=b'more', headers={'content-range': 'bytes 10-13/14'})
        if request.headers.get('range') == 'bytes=14-':
            return httpx.Response(416)
        return httpx.Response(200, content=b'0123456789')

    api = make_api(handler)
    assert await api.step_log('a', 'w', '{p}', '{s}') == (b'0123456789', 10)
    assert await api.step_log('a', 'w', '{p}', '{s}', 10) == (b'more', 14)
    assert await api.step_log('a', 'w', '{p}', '{s}', 14) == (b'', 14)
    assert seen == [None, 'bytes=10-', 'bytes=14-']


async def test_rerun_posts_the_same_target_and_stop():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.content))
        if request.url.path.endswith('/stopPipeline'):
            return httpx.Response(204)
        return httpx.Response(201, json=pipeline_json(8, 'PENDING'))

    api = make_api(handler)
    run = Pipeline.from_api(pipeline_json(6, 'FAILED'))
    new = await api.rerun_pipeline('acme', 'w', run)
    await api.stop_pipeline('acme', 'w', run.uuid)
    assert new.build_number == 8
    method, path, body = requests[0]
    assert (method, path) == ('POST', '/2.0/repositories/acme/w/pipelines/')
    assert json.loads(body) == {'target': run.target}
    assert requests[1][:2] == ('POST', '/2.0/repositories/acme/w/pipelines/{run-6}/stopPipeline')


async def test_latest_scheduled_run_matches_the_schedule():
    nightly = {'ref_name': 'master', 'selector': {'type': 'custom', 'pattern': 'nightly'}}
    runs = [
        pipeline_json(9, 'RUNNING'),  # a push build
        pipeline_json(
            8,
            'FAILED',
            trigger={'name': 'SCHEDULE'},
            target={'ref_name': 'master', 'selector': {'type': 'custom', 'pattern': 'other'}},
        ),
        pipeline_json(7, 'SUCCESSFUL', trigger={'name': 'SCHEDULE'}, target=nightly),
        pipeline_json(6, 'FAILED', trigger={'name': 'SCHEDULE'}, target=nightly),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/schedules'):
            return httpx.Response(
                200,
                json={
                    'values': [
                        {
                            'uuid': '{s}',
                            'enabled': True,
                            'cron_pattern': '0 51 4 * * ? *',
                            'target': {'ref_name': 'master', 'selector': nightly['selector']},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={'values': runs})

    api = make_api(handler)
    [schedule] = await api.schedules('acme', 'w')
    assert schedule == Schedule('{s}', True, '0 51 4 * * ? *', 'master', 'custom', 'nightly')
    run = await api.latest_scheduled_run('acme', 'w', schedule)
    assert run is not None and run.build_number == 7
