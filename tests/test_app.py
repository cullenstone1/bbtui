from textual.widgets import DataTable, ListView

from bbtui.app import BBTUI
from bbtui.config import Settings
from bbtui.models import Comment, DiffStat, PullRequest, Repository
from bbtui.screens import DashboardScreen, PullRequestDetailScreen, PullRequestsScreen
from bbtui.screens.pull_request_detail import known_names
from bbtui.widgets import CommentView, DiffView, comment_threads, resolve_mentions
from tests.factories import (
    comment_json,
    diffstat_json,
    pull_request_json,
    repository_json,
    user_json,
)

DIFF = (
    'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n tail\n'
    'diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-b-old\n+b-new\n'
)


class FakeAPI:
    def __init__(self):
        self.calls: list[tuple] = []

    async def aclose(self):
        pass

    async def repositories(self, names, default_workspace):
        found = [Repository.from_api(repository_json(name)) for name in names if name != 'ghost']
        return found, [name for name in names if name == 'ghost']

    async def recent_repositories(self, workspace, limit=10):
        return [Repository.from_api(repository_json(slug)) for slug in ('widgets', 'recent-one')]

    async def search_repositories(self, workspace, text, limit=50):
        self.calls.append(('search', text))
        return [Repository.from_api(repository_json(f'{text}-match'))]

    async def pull_requests(self, workspace, repo_slug, state='OPEN', limit=50):
        self.calls.append(('pull_requests', workspace, repo_slug, state))
        return [
            PullRequest.from_api(pull_request_json(i, f'{workspace}/{repo_slug}')) for i in (11, 12)
        ]

    async def pull_request(self, workspace, repo_slug, pr_id):
        return PullRequest.from_api(pull_request_json(pr_id, f'{workspace}/{repo_slug}'))

    async def pull_request_diffstat(self, workspace, repo_slug, pr_id):
        return [
            DiffStat.from_api(diffstat_json('modified', 'a.py', 'a.py', 1, 1)),
            DiffStat.from_api(diffstat_json('modified', 'b.py', 'b.py', 1, 1)),
        ]

    async def pull_request_comments(self, workspace, repo_slug, pr_id):
        return [
            Comment.from_api(comment_json(1, 'top')),
            Comment.from_api(comment_json(2, 'reply', parent=1)),
            Comment.from_api(
                comment_json(3, 'inline nit', inline={'path': 'a.py', 'to': 2, 'from': None})
            ),
            Comment.from_api(comment_json(4, 'inline reply', parent=3)),
            Comment.from_api(
                comment_json(5, 'outdated', inline={'path': 'a.py', 'to': 99, 'from': None})
            ),
        ]

    async def pull_request_diff(self, workspace, repo_slug, pr_id):
        self.calls.append(('diff', pr_id))
        return DIFF


def make_app(api: FakeAPI) -> BBTUI:
    settings = Settings(
        username='me', api_token='t', workspace='acme', starred_repos=['widgets', 'ghost']
    )
    return BBTUI(settings, api)  # type: ignore[arg-type]


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_dashboard_to_pull_request_detail_and_back():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, DashboardScreen)
        starred = screen.query_one('#starred', ListView)
        others = screen.query_one('#others', ListView)
        assert [item.repo.slug for item in starred.children] == ['widgets']
        # Starred repos are not repeated under "Recently updated".
        assert [item.repo.slug for item in others.children] == ['recent-one']

        await pilot.press('enter')
        await settle(app, pilot)
        assert isinstance(app.screen, PullRequestsScreen)
        assert ('pull_requests', 'acme', 'widgets', 'OPEN') in api.calls
        assert app.screen.query_one(DataTable).row_count == 2

        await pilot.press('enter')
        await settle(app, pilot)
        detail = app.screen
        assert isinstance(detail, PullRequestDetailScreen)
        assert detail.pr_id == 11
        # General comments are on the overview; inline ones are not.
        overview = list(detail.query_one('#general-comments').query(CommentView))
        assert [(card.comment.id, card.depth) for card in overview] == [(1, 0), (2, 1)]

        await pilot.press('escape')
        await pilot.pause()
        assert isinstance(app.screen, PullRequestsScreen)
        await pilot.press('escape')
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


async def test_diff_shows_one_file_with_inline_threads():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        app.push_screen(PullRequestDetailScreen('acme', 'widgets', 11))
        await settle(app, pilot)
        detail = app.screen
        await pilot.press('2')
        await settle(app, pilot)

        chooser = detail.query_one('#file-chooser', DataTable)
        assert chooser.row_count == 2
        assert app.focused is chooser
        view = detail.query_one(DiffView)
        kinds = [type(child).__name__ for child in view.children]
        # The outdated comment leads, then lines up to `+new`, its thread, then the rest.
        assert kinds == ['CommentView', 'Static', 'CommentView', 'CommentView', 'Static']
        assert [card.comment.id for card in view.query(CommentView)] == [5, 3, 4]
        lines = str(view.children[1].render())
        assert '+new' in lines and 'tail' not in lines
        assert 'b-new' not in str(view.children[4].render())

        await pilot.press('enter')
        await pilot.pause()
        assert app.focused is view
        await pilot.press('escape')
        await pilot.pause()
        assert app.focused is chooser
        assert app.screen is detail

        await pilot.press('right_square_bracket')
        await pilot.pause(0.2)
        await settle(app, pilot)
        assert chooser.cursor_row == 1
        assert 'b-new' in ''.join(str(child.render()) for child in view.children)
        assert not list(view.query(CommentView))


async def test_search_replaces_recent_list():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press('slash')
        await pilot.press(*'gadg', 'enter')
        await settle(app, pilot)
        others = app.screen.query_one('#others', ListView)
        assert [item.repo.slug for item in others.children] == ['gadg-match']
        assert ('search', 'gadg') in api.calls


async def test_pull_request_state_cycles():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        app.push_screen(PullRequestsScreen(Repository.from_api(repository_json('widgets'))))
        await settle(app, pilot)
        await pilot.press('s')
        await settle(app, pilot)
        assert ('pull_requests', 'acme', 'widgets', 'MERGED') in api.calls


def test_comments_are_threaded_oldest_first():
    comments = [
        Comment.from_api(comment_json(1, 'first')),
        Comment.from_api(comment_json(3, 'reply', parent=1)),
        Comment.from_api(comment_json(2, 'second thread')),
        Comment.from_api(comment_json(4, 'orphan', parent=99)),
    ]
    threads = [[(c.body, depth) for c, depth in t] for t in comment_threads(comments)]
    assert threads == [[('first', 0), ('reply', 1)], [('second thread', 0)], [('orphan', 0)]]


def test_mentions_resolve_to_known_names():
    names = {'712020:abc': 'Ada Okafor'}
    text = 'cc @{712020:abc} and @{unknown}'
    assert resolve_mentions(text, names) == 'cc @Ada Okafor and @{unknown}'


def test_known_names_cover_author_reviewers_and_commenters():
    pr = PullRequest.from_api(pull_request_json(1))
    names = known_names(pr, [Comment.from_api(comment_json(1, 'x', user=user_json('Eve')))])
    assert names['id-ada'] == 'Ada'
    assert names['id-cy'] == 'Cy'
    assert names['id-eve'] == 'Eve'
