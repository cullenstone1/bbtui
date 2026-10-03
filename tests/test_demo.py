"""The demo (scripts/demo.py) has data for everything the screenshots show."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import screenshots  # noqa: E402


async def test_screenshots_need_nothing_the_demo_lacks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = await screenshots.capture(tmp_path / 'shots')
    assert [path.stem for path in paths] == [
        'dashboard',
        'pull-request',
        'diff',
        'pipelines',
        'pipeline-run',
    ]
    dashboard = paths[0].read_text()
    assert 'Retry' in dashboard and 'acme' in dashboard
