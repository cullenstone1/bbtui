"""Save SVG screenshots of bbtui running on the demo data (see `demo.py`), for the README.

    python scripts/screenshots.py [output directory, default docs/screenshots]

Fails, without writing anything, if the app asked the demo for something it had no data for,
so a screenshot never shows an error or an empty panel by accident.
"""

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo  # noqa: E402

SIZE = (140, 36)
REPO_ROOT = Path(__file__).resolve().parent.parent


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause(0.3)


async def capture(out: Path) -> list[Path]:
    from bbtui.models import Repository
    from bbtui.screens.pipeline_run import PipelineRunScreen
    from bbtui.screens.pipelines import PipelinesScreen
    from bbtui.screens.pull_request_detail import PullRequestDetailScreen

    server = demo.DemoBitbucket()
    app = demo.demo_app(server)
    shots: list[tuple[str, str]] = []  # (file name, SVG)
    payments = Repository.from_api(demo.repository('payments-api'))

    async def shot(name: str) -> None:
        await settle(app, pilot)
        shots.append((name, app.export_screenshot(title=f'bbtui: {name}')))

    async with app.run_test(size=SIZE) as pilot:
        await shot('dashboard')

        app.push_screen(PullRequestDetailScreen(demo.WORKSPACE, 'payments-api', 142))
        await shot('pull-request')
        await pilot.press('2')
        await shot('diff')
        await pilot.press('escape')

        app.push_screen(PipelinesScreen(payments))
        await shot('pipelines')
        app.push_screen(PipelineRunScreen(demo.WORKSPACE, 'payments-api', 2215))
        await shot('pipeline-run')

    if server.unhandled:
        missing = '\n  '.join(dict.fromkeys(server.unhandled))
        raise SystemExit(f'No screenshots written; the demo had no data for:\n  {missing}')
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, svg in shots:
        path = out / f'{name}.svg'
        path.write_text(svg)
        paths.append(path)
    return paths


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / 'docs' / 'screenshots'
    out = out.resolve()  # Before `isolate()` changes directory.
    demo.isolate()
    for path in asyncio.run(capture(out)):
        sys.stdout.write(f'{path}\n')


if __name__ == '__main__':
    main()
