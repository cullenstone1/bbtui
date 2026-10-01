from textual.widgets import (
    DataTable,
    Input,
    Label,
    ListView,
    SelectionList,
    Static,
    TextArea,
)

from bbtui.api import PermissionDeniedError
from bbtui.app import BBTUI
from bbtui.config import Settings
from bbtui.models import (
    Branch,
    BuildStatus,
    Comment,
    Commit,
    DiffStat,
    PullRequest,
    Repository,
    User,
)
from bbtui.screens import DashboardScreen, PullRequestDetailScreen, PullRequestsScreen
from bbtui.screens.composer import CommentComposer
from bbtui.screens.create_pull_request import CreatePullRequestScreen
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
    def __init__(self, me: str = 'Bob'):
        self.calls: list[tuple] = []
        self.me = me
        self.posted: list[Comment] = []

    async def current_user(self):
        return User.from_api({**user_json(self.me), 'uuid': f'{{{self.me.lower()}}}'})

    async def pull_requests_by(self, workspace, user_uuid, state='OPEN', limit=50):
        self.calls.append(('pull_requests_by', workspace, user_uuid))
        return [
            PullRequest.from_api(pull_request_json(21, 'acme/widgets', title='Mine', draft=True)),
            PullRequest.from_api(pull_request_json(22, 'other/gadgets', participants=[])),
        ]

    async def approve(self, workspace, repo_slug, pr_id):
        self.calls.append(('approve', pr_id))

    async def unapprove(self, workspace, repo_slug, pr_id):
        self.calls.append(('unapprove', pr_id))

    async def request_changes(self, workspace, repo_slug, pr_id):
        self.calls.append(('request_changes', pr_id))

    async def remove_request_changes(self, workspace, repo_slug, pr_id):
        self.calls.append(('remove_request_changes', pr_id))

    async def create_comment(self, workspace, repo_slug, pr_id, body, **location):
        self.calls.append(('comment', body, location))
        inline = None
        if location.get('path'):
            inline = {
                'path': location['path'],
                'to': location.get('line_to'),
                'from': location.get('line_from'),
            }
        comment = Comment.from_api(
            comment_json(
                100 + len(self.posted), body, parent=location.get('parent_id'), inline=inline
            )
        )
        self.posted.append(comment)
        return comment

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
            *self.posted,
        ]

    async def pull_request_statuses(self, workspace, repo_slug, pr_id):
        if repo_slug == 'gadgets':
            raise PermissionDeniedError('no pipelines here', 403)
        return [
            BuildStatus(
                key='k',
                name='Pipeline',
                state='FAILED',
                url='https://bitbucket.org/acme/widgets/pipelines/results/42',
            )
        ]

    async def pull_request_open_task_count(self, workspace, repo_slug, pr_id):
        raise PermissionDeniedError('Permission denied: no tasks scope', 403)

    async def branches(self, workspace, repo_slug, search='', limit=30):
        names = ['develop', 'ABC-9-new-thing', 'ABC-8-old-thing']
        return [Branch(name) for name in names if search.lower() in name.lower()]

    async def branch_exists(self, workspace, repo_slug, name):
        return True

    async def development_branch(self, workspace, repo_slug):
        return 'develop'

    async def default_reviewers(self, workspace, repo_slug):
        return [
            User.from_api({**user_json(name), 'uuid': f'{{{name.lower()}}}'})
            for name in (self.me, 'Cy')
        ]

    async def commits_between(self, workspace, repo_slug, source, destination, limit=100):
        return [Commit(hash='abcdef123', message=f'{source} work\n\nbody')]

    async def diffstat_between(self, workspace, repo_slug, source, destination):
        return [DiffStat.from_api(diffstat_json('modified', 'a.py', 'a.py', 5, 2))]

    async def open_pull_requests_from(self, workspace, repo_slug, source):
        return []

    async def create_pull_request(self, workspace, repo_slug, **fields):
        self.calls.append(('create', fields))
        return PullRequest.from_api(pull_request_json(77, f'{workspace}/{repo_slug}'))

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
        # Builds and merge checks; the failing task lookup leaves the rest of the page intact.
        checks = str(detail.query_one('#merge-checks', Static).render())
        assert 'Changes requested by Cy' in checks
        assert '1 of 1 build failed' in checks
        assert 'open task' not in checks
        assert '#42' in str(detail.query_one('#builds', Static).render())
        opened = []
        app.open_url = lambda url, **kwargs: opened.append(url)
        await pilot.press('p')
        assert opened == ['https://bitbucket.org/acme/widgets/pipelines/results/42']
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
        assert kinds == ['CommentView', 'DiffLines', 'CommentView', 'CommentView', 'DiffLines']
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


async def open_detail(app, pilot) -> PullRequestDetailScreen:
    await settle(app, pilot)
    app.push_screen(PullRequestDetailScreen('acme', 'widgets', 11))
    await settle(app, pilot)
    assert isinstance(app.screen, PullRequestDetailScreen)
    return app.screen


async def test_approve_toggles_based_on_your_current_review():
    # Bob has approved and Cy has requested changes on the fake PR.
    api = FakeAPI(me='Bob')
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_detail(app, pilot)
        await pilot.press('a')
        await settle(app, pilot)
        await pilot.press('x')
        await settle(app, pilot)
    assert ('unapprove', 11) in api.calls
    assert ('request_changes', 11) in api.calls

    api = FakeAPI(me='Cy')
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_detail(app, pilot)
        await pilot.press('a')
        await settle(app, pilot)
        await pilot.press('x')
        await settle(app, pilot)
    assert ('approve', 11) in api.calls
    assert ('remove_request_changes', 11) in api.calls


async def type_comment(app, pilot, text: str, key: str = 'ctrl+s') -> None:
    await pilot.press('c')
    await pilot.pause()
    assert isinstance(app.screen, CommentComposer)
    app.screen.query_one(TextArea).insert(text)
    await pilot.press(key)
    await settle(app, pilot)


async def test_general_comment_from_overview():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)
        await type_comment(app, pilot, 'Looks good')
        assert (
            'comment',
            'Looks good',
            {'path': None, 'line_to': None, 'line_from': None, 'parent_id': None},
        ) in api.calls
        # The new comment shows up without reloading the page.
        bodies = [c.comment.body for c in detail.query_one('#general-comments').query(CommentView)]
        assert 'Looks good' in bodies


async def test_inline_comment_on_cursor_line_keeps_the_view():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)
        await pilot.press('2')
        await settle(app, pilot)
        await pilot.press('enter')
        await pilot.pause()
        view = detail.query_one(DiffView)
        assert app.focused is view
        # The cursor starts on the first code line (` ctx`); down once is `-old`.
        assert view.cursor_line.text == ' ctx'
        await pilot.press('down')
        assert view.cursor_line.text == '-old'
        await type_comment(app, pilot, 'why remove this?')
        assert (
            'comment',
            'why remove this?',
            {'path': 'a.py', 'line_to': None, 'line_from': 2, 'parent_id': None},
        ) in api.calls
        # Same file, same cursor, and the new thread is rendered.
        assert view.cursor_line.text == '-old'
        assert 'why remove this?' in [c.comment.body for c in view.query(CommentView)]


async def test_reply_to_focused_comment_and_drafts_survive_cancel():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)
        card = next(c for c in detail.query(CommentView) if c.comment.id == 1)
        card.focus()
        await pilot.pause()
        await type_comment(app, pilot, 'half a thought', key='escape')
        assert not [c for c in api.calls if c[0] == 'comment']

        card.focus()
        await pilot.press('c')
        await pilot.pause()
        editor = app.screen.query_one(TextArea)
        assert editor.text == 'half a thought'
        editor.insert(', finished')
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert (
            'comment',
            'half a thought, finished',
            {'path': None, 'line_to': None, 'line_from': None, 'parent_id': 1},
        ) in api.calls
        assert detail.drafts == {}


async def test_comment_on_a_hunk_header_is_refused():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)
        await pilot.press('2')
        await settle(app, pilot)
        await pilot.press('enter', 'home')
        await pilot.pause()
        assert detail.query_one(DiffView).cursor_line.kind == 'hunk'
        await pilot.press('c')
        await pilot.pause()
        assert app.screen is detail


async def test_create_pull_request_flow():
    api = FakeAPI(me='Bob')
    app = make_app(api)
    async with app.run_test(size=(120, 50)) as pilot:
        await settle(app, pilot)
        app.push_screen(PullRequestsScreen(Repository.from_api(repository_json('widgets'))))
        await settle(app, pilot)
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        assert isinstance(form, CreatePullRequestScreen)
        assert form.destination().chosen == 'develop'
        # Default reviewers are pre-selected, without yourself.
        assert form.query_one(SelectionList).selected == ['{cy}']

        # The source input has focus; filter and pick the branch.
        assert app.focused is form.source().query_one(Input)
        await pilot.press(*'new', 'enter')
        await pilot.pause(0.4)
        await settle(app, pilot)
        assert form.source().chosen == 'ABC-9-new-thing'
        # One commit: its summary is the title, and it is listed in the description.
        assert form.query_one('#create-title', Input).value == 'ABC-9-new-thing work'
        assert form.query_one('#create-description', TextArea).text == '* ABC-9-new-thing work'
        assert '1 file changed' in str(form.query_one('#create-preview', Static).render())

        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert (
            'create',
            {
                'title': 'ABC-9-new-thing work',
                'source': 'ABC-9-new-thing',
                'destination': 'develop',
                'description': '* ABC-9-new-thing work',
                'reviewer_uuids': ['{cy}'],
                'close_source_branch': False,
                'draft': False,
            },
        ) in api.calls
        assert isinstance(app.screen, PullRequestDetailScreen)
        assert app.screen.pr_id == 77


async def test_create_keeps_your_edits_and_confirms_discarding_them():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 50)) as pilot:
        await settle(app, pilot)
        form = CreatePullRequestScreen(Repository.from_api(repository_json('widgets')))
        app.push_screen(form)
        await settle(app, pilot)
        form.query_one('#create-title', Input).value = 'My own title'
        form.source().choose('ABC-8-old-thing')
        await settle(app, pilot)
        assert form.query_one('#create-title', Input).value == 'My own title'
        assert form.query_one('#create-description', TextArea).text == '* ABC-8-old-thing work'

        form.query_one('#create-description', TextArea).focus()
        await pilot.press('escape')
        await pilot.pause()
        assert app.screen is form
        await pilot.press('escape')
        await pilot.pause()
        assert app.screen is not form


async def test_my_pull_requests_on_the_dashboard():
    api = FakeAPI(me='Ada')
    app = make_app(api)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)
        mine = app.screen.query_one('#mine', ListView)
        assert ('pull_requests_by', 'acme', '{ada}') in api.calls
        items = list(mine.children)
        assert [item.pr.id for item in items] == [21, 22]
        first = [str(label.render()) for label in items[0].query(Label)]
        assert first[0] == '#21 [draft] Mine'
        # Same workspace shows the slug; reviews, comments and the failing build are summarised.
        assert first[1].startswith('widgets · ✔ 1/2 ✗1 · 💬 2 · ✗ build · ')
        second = [str(label.render()) for label in items[1].query(Label)]
        # Another workspace shows the full name; a failed build lookup just leaves builds out.
        assert second[1].startswith('other/gadgets · ✔ 0/0 · 💬 2 · ')
        assert 'build' not in second[1]

        mine.focus()
        await pilot.press('enter')
        await settle(app, pilot)
        assert isinstance(app.screen, PullRequestDetailScreen)
        assert (app.screen.repo_slug, app.screen.pr_id) == ('widgets', 21)
        await pilot.press('escape')
        await settle(app, pilot)
        assert isinstance(app.screen, DashboardScreen)
        # Coming back refreshes the list.
        assert api.calls.count(('pull_requests_by', 'acme', '{ada}')) == 2
