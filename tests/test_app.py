import sys
import time

from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    ListView,
    OptionList,
    RadioSet,
    SelectionList,
    Static,
    TextArea,
)

from bbtui.api import NotFoundError, PermissionDeniedError
from bbtui.app import BBTUI
from bbtui.config import Settings
from bbtui.history import pull_request_history
from bbtui.models import (
    Branch,
    BuildStatus,
    Comment,
    Commit,
    DiffStat,
    Pipeline,
    PipelineStep,
    PullRequest,
    Repository,
    Schedule,
    User,
)
from bbtui.screens import DashboardScreen, PullRequestDetailScreen, PullRequestsScreen
from bbtui.screens.commits import BranchChoiceScreen, CommitScreen, CommitsScreen
from bbtui.screens.composer import CommentComposer
from bbtui.screens.confirm import ConfirmScreen
from bbtui.screens.create_pull_request import CreatePullRequestScreen
from bbtui.screens.merge import MergeScreen
from bbtui.screens.pipeline_run import PipelineRunScreen
from bbtui.screens.pipelines import PipelinesScreen
from bbtui.screens.pull_request_detail import known_names
from bbtui.screens.url import CloneScreen, UrlScreen
from bbtui.widgets import CommentView, DiffView, comment_threads, resolve_mentions
from bbtui.widgets.diff_view import DiffLines
from bbtui.widgets.log_view import LogView
from tests.factories import (
    comment_json,
    diffstat_json,
    pipeline_json,
    pull_request_json,
    repository_json,
    step_json,
    user_json,
)

DIFF = (
    'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n tail\n'
    'diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-b-old\n+b-new\n'
)


def update_json(user: str, date: str, commit: str, **fields) -> dict:
    return {
        'update': {
            'author': user_json(user),
            'date': f'2026-09-{date}+00:00',
            'source': {'commit': {'hash': commit}},
            'draft': False,
            'changes': {},
            **fields,
        }
    }


ACTIVITY = [  # Newest first, as Bitbucket sends it.
    {'approval': {'user': user_json('Bob'), 'date': '2026-09-30T09:00:00+00:00'}},
    update_json('Ada', '29T12:00:00', 'c3', changes={'draft': {'old': True, 'new': False}}),
    update_json('Ada', '29T11:30:00', 'c3'),
    update_json('Ada', '29T11:00:00', 'c2'),
    update_json('Ada', '29T10:00:01', 'c1', changes={'reviewers': {'added': [user_json('Bob')]}}),
    update_json('Ada', '29T10:00:00', 'c1', draft=True),
]


class FakeAPI:
    def __init__(self, me: str = 'Bob'):
        self.calls: list[tuple] = []
        self.me = me
        self.posted: list[Comment] = []
        self.run_43_done = False
        self.draft = False
        self.merged = False

    async def current_user(self):
        return User.from_api({**user_json(self.me), 'uuid': f'{{{self.me.lower()}}}'})

    async def pull_requests_by(self, workspace, user_uuid, state='OPEN', limit=50):
        self.calls.append(('pull_requests_by', workspace, user_uuid))
        return [
            PullRequest.from_api(pull_request_json(21, 'acme/widgets', title='Mine', draft=True)),
            PullRequest.from_api(pull_request_json(22, 'other/gadgets', participants=[])),
        ]

    async def pull_requests_reviewed_by(self, repos, user_uuid, limit=50):
        self.calls.append(('pull_requests_reviewed_by', tuple(repos), user_uuid))
        me = {'user': {**user_json(self.me), 'uuid': user_uuid}, 'role': 'REVIEWER'}
        return [
            PullRequest.from_api(
                pull_request_json(31, 'acme/widgets', title='Please look', participants=[me])
            ),
            PullRequest.from_api(
                pull_request_json(32, 'acme/widgets', participants=[{**me, 'approved': True}])
            ),
            PullRequest.from_api(pull_request_json(33, 'acme/widgets', draft=True)),
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

    async def repository(self, workspace, repo_slug):
        self.calls.append(('repository', workspace, repo_slug))
        clone = [
            {'name': 'https', 'href': f'https://ada@bitbucket.org/{workspace}/{repo_slug}.git'},
            {'name': 'ssh', 'href': f'git@bitbucket.org:{workspace}/{repo_slug}.git'},
        ]
        data = repository_json(repo_slug, workspace)
        return Repository.from_api({**data, 'links': {**data['links'], 'clone': clone}})

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
        state = 'MERGED' if self.merged else 'OPEN'
        return PullRequest.from_api(
            pull_request_json(pr_id, f'{workspace}/{repo_slug}', draft=self.draft, state=state)
        )

    async def set_draft(self, workspace, repo_slug, pr_id, title, draft):
        self.calls.append(('set_draft', pr_id, draft))
        self.draft = draft
        return await self.pull_request(workspace, repo_slug, pr_id)

    async def merge_strategies(self, workspace, repo_slug, branch):
        return ['merge_commit', 'squash', 'fast_forward'], 'squash'

    async def merge_pull_request(self, workspace, repo_slug, pr_id, **choice):
        self.calls.append(('merge', pr_id, choice))
        self.merged = True
        return await self.pull_request(workspace, repo_slug, pr_id)

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
                comment_json(
                    5,
                    'outdated',
                    inline={
                        'path': 'a.py',
                        'to': 2,
                        'from': None,
                        'outdated': True,
                        'context_lines': '--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n ctx\n+older',
                    },
                )
            ),
            Comment.from_api(
                comment_json(
                    6,
                    'settled question\n\nmore detail',
                    resolution={
                        'type': 'comment_resolution',
                        'user': user_json('Cy'),
                        'created_on': '2026-09-30T12:00:00+00:00',
                    },
                )
            ),
            Comment.from_api(comment_json(7, 'settled answer', parent=6)),
            *self.posted,
        ]

    async def pull_request_history(self, workspace, repo_slug, pr_id):
        return pull_request_history(ACTIVITY)

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

    TAGS = [
        Branch('v1.0', is_tag=True, target='dev68' + 'f' * 34),
        Branch('v1.0-final', is_tag=True, target='dev68' + 'f' * 34),
    ]

    async def refs(self, workspace, repo_slug, search='', limit=30):
        self.calls.append(('refs', search))
        branches = await self.branches(workspace, repo_slug, search)
        return branches + [tag for tag in self.TAGS if search.lower() in tag.name.lower()]

    async def tags(self, workspace, repo_slug, limit=1000):
        self.calls.append(('tags',))
        return self.TAGS

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

    async def commits(self, workspace, repo_slug, branch, exclude=None, page=None, pagelen=30):
        self.calls.append(('commits', branch, exclude, page))
        if branch == 'nope':
            raise NotFoundError('Not found', 404)
        count = 3 if exclude else 70  # 3 commits ahead of `exclude`, else a long history.
        start = int(page.split(':')[1]) if page else 0
        commits = [
            Commit(
                hash=f'{branch[:3]}{i:02d}' + 'f' * 34,
                message=f'Change {i} on {branch}\n\nWhy {i}',
                author='Ada',
                parents=('p' * 40,),
            )
            for i in range(count - 1 - start, max(-1, count - 1 - start - pagelen), -1)
        ]
        more = start + pagelen < count
        return commits, (f'page:{start + pagelen}' if more else None)

    async def commit_diffstat(self, workspace, repo_slug, commit):
        self.calls.append(('commit_diffstat', commit))
        return await self.pull_request_diffstat(workspace, repo_slug, 0)

    async def commit_diff(self, workspace, repo_slug, commit):
        return DIFF

    async def diffstat_between(self, workspace, repo_slug, source, destination):
        return [DiffStat.from_api(diffstat_json('modified', 'a.py', 'a.py', 5, 2))]

    async def open_pull_requests_from(self, workspace, repo_slug, source):
        return []

    async def create_pull_request(self, workspace, repo_slug, **fields):
        self.calls.append(('create', fields))
        return PullRequest.from_api(pull_request_json(77, f'{workspace}/{repo_slug}'))

    # pipelines: run 42 failed (two steps, the second failed); run 43 is running
    LOG = (
        'compiling\n'
        'src/a.cpp:1: error: first problem\n'
        + ''.join(f'line {i}\n' for i in range(100))
        + '\x1b[1;31m[FAIL] the real problem\x1b[0m\n'
        'done\n'
    )

    def run_json(self, number):
        if number == 43:
            return pipeline_json(43, 'SUCCESSFUL' if self.run_43_done else 'RUNNING')
        return pipeline_json(number, 'FAILED')

    async def pipeline(self, workspace, repo_slug, run):
        self.calls.append(('pipeline', repo_slug, run))
        return Pipeline.from_api(self.run_json(int(run)))

    async def pipelines(self, workspace, repo_slug, limit=50):
        return [
            Pipeline.from_api(self.run_json(43)),
            Pipeline.from_api(pipeline_json(42, 'FAILED', creator=user_json('Cy'))),
            Pipeline.from_api(pipeline_json(41, 'SUCCESSFUL')),
        ]

    async def pipeline_steps(self, workspace, repo_slug, pipeline_uuid):
        if pipeline_uuid == '{run-43}':
            return [
                PipelineStep.from_api(
                    step_json('Build', 'SUCCESSFUL' if self.run_43_done else 'RUNNING')
                )
            ]
        return [
            PipelineStep.from_api(step_json('Lint')),
            PipelineStep.from_api(step_json('Build and Test', 'FAILED')),
        ]

    async def step_log(self, workspace, repo_slug, pipeline_uuid, step_uuid, start=0):
        self.calls.append(('step_log', step_uuid, start))
        if pipeline_uuid == '{run-43}':
            data = b'starting\n' + (b'finished\n' if self.run_43_done else b'')
        else:
            data = self.LOG.encode() if step_uuid == '{step-Build and Test}' else b'lint ok\n'
        return data[start:], len(data)

    async def rerun_pipeline(self, workspace, repo_slug, run):
        self.calls.append(('rerun', run.build_number))
        return Pipeline.from_api(pipeline_json(44, 'PENDING'))

    async def stop_pipeline(self, workspace, repo_slug, pipeline_uuid):
        self.calls.append(('stop', pipeline_uuid))

    async def schedules(self, workspace, repo_slug):
        if repo_slug != 'widgets':
            return []
        return [Schedule('{s}', True, '0 51 4 * * ? *', 'master', 'custom', 'nightly')]

    async def latest_scheduled_run(self, workspace, repo_slug, schedule):
        return Pipeline.from_api(
            pipeline_json(
                40,
                'FAILED',
                trigger={'name': 'SCHEDULE'},
                target={'ref_name': 'master', 'selector': {'type': 'custom', 'pattern': 'nightly'}},
            )
        )

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
        # `p` opens the Bitbucket Pipelines build in bbtui.
        await pilot.press('p')
        await settle(app, pilot)
        assert isinstance(app.screen, PipelineRunScreen)
        assert app.screen.build_number == 42
        assert app.screen.label == '#11 PR 11'
        await pilot.press('escape')
        await settle(app, pilot)
        # General comments are on the overview; inline ones are not.
        overview = list(detail.query_one('#general-comments').query(CommentView))
        assert [(card.comment.id, card.depth) for card in overview] == [
            (1, 0),
            (2, 1),
            (6, 0),
            (7, 1),
        ]

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
        lines = view.children[1].plain
        assert '+new' in lines and 'tail' not in lines
        assert 'b-new' not in view.children[4].plain

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
        assert 'b-new' in ''.join(lines.plain for lines in view.query(DiffLines))
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


async def test_commenting_on_a_later_file_keeps_the_file_and_scroll(monkeypatch):
    body = ''.join(f'+line {i}\n' for i in range(300))
    long_b = f'diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -0,0 +1,300 @@\n{body}'
    monkeypatch.setattr(sys.modules[__name__], 'DIFF', DIFF.split('diff --git a/b.py')[0] + long_b)
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)
        await pilot.press('2', 'right_square_bracket')
        await pilot.pause(0.2)
        await settle(app, pilot)
        await pilot.press('enter')
        view = detail.query_one(DiffView)
        view.move_cursor(150)
        await pilot.pause()
        cursor, scroll_y = view.cursor, view.scroll_y
        assert scroll_y > 0
        await type_comment(app, pilot, 'deep in b.py')
        await pilot.pause(0.2)
        await settle(app, pilot)
        # Still on b.py, same line, same place on screen (it used to jump to a.py's top).
        assert detail.shown_file == 1
        assert detail.query_one('#file-chooser', DataTable).cursor_row == 1
        assert (view.cursor, view.scroll_y) == (cursor, scroll_y)
        assert 'deep in b.py' in [c.comment.body for c in view.query(CommentView)]


async def test_history_on_the_overview():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)
        history = str(detail.query_one('#history', Static).render())
        lines = [line.split('  ', 1)[1] for line in history.splitlines()]
        assert lines == [
            '● Ada opened as a draft',
            '· Ada added reviewers Bob',
            '↑ Ada pushed 2 times, latest c3',
            '· Ada marked ready',
            '✔ Bob approved',
        ]


async def test_outdated_and_resolved_comments():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)
        # Resolved thread: collapsed to its first line, replies hidden; Enter expands it.
        cards = {c.comment.id: c for c in detail.query_one('#general-comments').query(CommentView)}
        root, reply = cards[6], cards[7]
        assert root.has_class('resolved')
        assert root.source == 'settled question'
        assert not reply.display
        assert 'resolved by Cy' in str(root.border_subtitle)
        assert '1 reply hidden' in str(root.border_subtitle)
        assert not cards[1].has_class('resolved') and cards[2].display
        root.focus()
        await pilot.press('enter')
        await pilot.pause()
        assert reply.display
        assert 'more detail' in root.source
        assert 'hidden' not in str(root.border_subtitle)
        await pilot.press('enter')
        await pilot.pause()
        assert not reply.display

        # Outdated inline comment: labelled, first in the file, with the code it was made on.
        await pilot.press('2')
        await settle(app, pilot)
        view = detail.query_one(DiffView)
        outdated = view.query(CommentView).first()
        assert outdated.comment.id == 5
        assert 'outdated' in str(outdated.border_title)
        assert outdated.source.startswith('```diff\n ctx\n+older\n```')


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

        # Your edits also hold off the idle timeout.
        app.settings.idle_timeout_minutes = 1
        app.last_interaction = time.monotonic() - 3600
        await app.check_idle_timeout()
        assert app.screen is form

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


async def test_pull_requests_to_review_on_the_dashboard():
    api = FakeAPI(me='Ada')
    app = make_app(api)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)
        review = app.screen.query_one('#review', ListView)
        # Starred repositories first, then recently updated ones.
        repos = (
            ('acme', 'widgets'),
            ('acme', 'ghost'),
            ('acme', 'widgets'),
            ('acme', 'recent-one'),
        )
        assert ('pull_requests_reviewed_by', repos, '{ada}') in api.calls
        # Already approved and draft pull requests aren't waiting for you.
        items = list(review.children)
        assert [item.pr.id for item in items] == [31]
        labels = [str(label.render()) for label in items[0].query(Label)]
        assert labels[0] == '#31 Please look'
        assert labels[1].startswith('widgets · Ada · ✔ 0/1 · ')

        review.focus()
        await pilot.press('enter')
        await settle(app, pilot)
        assert isinstance(app.screen, PullRequestDetailScreen)
        assert app.screen.pr_id == 31
        await pilot.press('escape')
        await settle(app, pilot)
        calls = [call for call in api.calls if call[0] == 'pull_requests_reviewed_by']
        assert len(calls) == 2  # Coming back refreshes the list.
        await pilot.press('r')
        await settle(app, pilot)
        calls = [call for call in api.calls if call[0] == 'pull_requests_reviewed_by']
        assert len(calls) == 3  # So does `r`.


async def test_draft_pull_requests_to_review_are_optional():
    app = make_app(FakeAPI(me='Ada'))
    app.settings.review_include_drafts = True
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)
        review = app.screen.query_one('#review', ListView)
        assert [item.pr.id for item in review.children] == [31, 33]


async def open_run(app, pilot, number: int) -> PipelineRunScreen:
    await settle(app, pilot)
    app.push_screen(PipelineRunScreen('acme', 'widgets', number, 'label'))
    await settle(app, pilot)
    assert isinstance(app.screen, PipelineRunScreen)
    return app.screen


async def test_failed_run_lands_on_the_last_likely_failure():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(140, 45)) as pilot:
        screen = await open_run(app, pilot, 42)
        log = screen.query_one(LogView)
        # The failed step is shown, and the cursor is on its last failure line.
        assert screen.step_index == 1
        assert len(log.lines) == 104
        assert log.failures == [1, 102]
        assert log.current == 102
        assert app.focused is log
        # Scrolled so the failure is in view.
        assert (
            log.scroll_offset.y <= 102 < log.scroll_offset.y + log.scrollable_content_region.height
        )
        failures = screen.query_one('#run-failures', DataTable)
        assert failures.display and failures.row_count == 2

        await pilot.press('e')
        assert log.current == 1  # Wraps around to the first failure.
        await pilot.press('E')
        assert log.current == 102

        await pilot.press('slash')
        await pilot.press(*'line 50', 'enter')
        await pilot.pause()
        assert log.current == 52
        await pilot.press('n')
        assert log.current == 52  # Only one match.

        # Picking the other step loads its log.
        screen.query_one('#run-steps', DataTable).focus()
        await pilot.press('up', 'enter')
        await settle(app, pilot)
        assert screen.step_index == 0
        assert log.lines == ['lint ok']


async def test_rerun_and_stop_ask_first():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(140, 45)) as pilot:
        await open_run(app, pilot, 42)
        await pilot.press('R')
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press('n')
        await pilot.pause()
        assert ('rerun', 42) not in api.calls

        await pilot.press('R')
        await pilot.pause()
        await pilot.press('y')
        await settle(app, pilot)
        assert ('rerun', 42) in api.calls
        assert isinstance(app.screen, PipelineRunScreen) and app.screen.build_number == 44

    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(140, 45)) as pilot:
        await open_run(app, pilot, 43)
        await pilot.press('s')
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press('y')
        await settle(app, pilot)
        assert ('stop', '{run-43}') in api.calls


async def test_running_run_tails_its_log_and_notifies_when_done():
    api = FakeAPI()
    app = make_app(api)
    notes = []
    async with app.run_test(size=(140, 45)) as pilot:
        app.notify = lambda message, **kwargs: notes.append(message)
        screen = await open_run(app, pilot, 43)
        log = screen.query_one(LogView)
        assert log.lines == ['starting']
        assert screen.poller is not None

        api.run_43_done = True
        screen.poll()
        await settle(app, pilot)
        assert log.lines == ['starting', 'finished']
        # Only the new bytes were fetched.
        assert ('step_log', '{step-Build}', len(b'starting\n')) in api.calls
        assert screen.poller is None
        assert notes == ['✔ Build passed: label']


async def test_pipelines_list_filters_and_opens_runs():
    api = FakeAPI(me='Ada')
    app = make_app(api)
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        app.push_screen(PullRequestsScreen(Repository.from_api(repository_json('widgets'))))
        await settle(app, pilot)
        await pilot.press('P')
        await settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, PipelinesScreen)
        table = screen.query_one(DataTable)
        assert table.row_count == 3
        await pilot.press('f')
        assert table.row_count == 1  # Only the failed run.
        await pilot.press('m')
        assert table.row_count == 0  # Run 42 was Cy's.
        await pilot.press('f')
        assert table.row_count == 2  # Mine: 43 and 41.
        await pilot.press('enter')
        await settle(app, pilot)
        assert isinstance(app.screen, PipelineRunScreen)
        assert app.screen.build_number == 43


async def test_scheduled_pipelines_on_the_dashboard():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        nightly = app.screen.query_one('#nightly', ListView)
        assert nightly.display
        [item] = nightly.children
        labels = [str(label.render()) for label in item.query(Label)]
        assert labels[0] == 'widgets  nightly on master'
        assert labels[1].startswith('✗ failed  #40 · ')
        assert '1 failed' in str(nightly.border_subtitle)
        nightly.focus()
        await pilot.press('enter')
        await settle(app, pilot)
        assert isinstance(app.screen, PipelineRunScreen)
        assert app.screen.build_number == 40


async def test_running_builds_on_your_prs_are_watched():
    api = FakeAPI(me='Ada')
    app = make_app(api)
    notes = []

    async def statuses(workspace, repo_slug, pr_id):
        # PR 21's build is running until `run_43_done`; PR 22's has already passed.
        state = 'SUCCESSFUL' if api.run_43_done or pr_id == 22 else 'INPROGRESS'
        return [
            BuildStatus(
                key='k',
                name='Pipeline',
                state=state,
                url=f'https://bitbucket.org/acme/widgets/pipelines/results/{pr_id + 22}',
            )
        ]

    api.pull_request_statuses = statuses
    async with app.run_test(size=(160, 45)) as pilot:
        app.notify = lambda message, **kwargs: notes.append(message)
        await settle(app, pilot)
        assert 'https://bitbucket.org/acme/widgets/pipelines/results/43' in app.watched_builds
        app.check_watched_builds()
        await settle(app, pilot)
        assert notes == []
        api.run_43_done = True
        app.check_watched_builds()
        await settle(app, pilot)
        assert notes == ['✔ Build passed: #21 Mine']
        assert app.watched_builds == {}


async def test_mark_ready_and_back_to_draft():
    api = FakeAPI()
    api.draft = True
    app = make_app(api)
    async with app.run_test(size=(120, 45)) as pilot:
        detail = await open_detail(app, pilot)
        # Drafts can't be merged.
        await pilot.press('m')
        await pilot.pause()
        assert app.screen is detail
        await pilot.press('d')
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press('y')
        await settle(app, pilot)
        assert ('set_draft', 11, False) in api.calls
        assert detail.pull_request is not None and not detail.pull_request.draft

        await pilot.press('d', 'y')
        await settle(app, pilot)
        assert ('set_draft', 11, True) in api.calls


async def test_merge_dialog():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 50)) as pilot:
        detail = await open_detail(app, pilot)
        await pilot.press('m')
        await settle(app, pilot)
        dialog = app.screen
        assert isinstance(dialog, MergeScreen)
        # The repository's default strategy is chosen, and blockers (Cy requested changes,
        # the build failed) turn the button into "Merge anyway".
        assert dialog.strategy == 'squash'
        assert str(dialog.query_one('#merge-confirm', Button).label).startswith('Merge anyway')
        message = dialog.query_one('#merge-message', TextArea)
        assert message.text.startswith('Merged in feature/11 (pull request #11)\n\nPR 11')
        assert 'Approved-by: Bob' in message.text

        # Fast-forward takes no message.
        dialog.query_one(RadioSet).focus()
        await pilot.press('down', 'down', 'enter')
        await pilot.pause()
        assert dialog.strategy == 'fast_forward'
        assert not message.display

        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert (
            'merge',
            11,
            {'strategy': 'fast_forward', 'message': None, 'close_source_branch': False},
        ) in api.calls
        assert app.screen is detail
        assert detail.pull_request is not None and detail.pull_request.state == 'MERGED'
        await pilot.press('m')
        await pilot.pause()
        assert app.screen is detail  # Already merged.


async def show_url(app, pilot) -> str:
    await pilot.press('u')
    await pilot.pause()
    assert isinstance(app.screen, UrlScreen)
    url = app.screen.url
    await pilot.press('escape')
    await pilot.pause()
    return url


async def test_clone_links(monkeypatch):
    api = FakeAPI(me='Ada')
    app = make_app(api)
    copied: list[str] = []
    async with app.run_test(size=(160, 45)) as pilot:
        monkeypatch.setattr('bbtui.screens.url.copy_with_tool', copied.append)
        await settle(app, pilot)

        # A repository on the dashboard; without clone links from the API, they're built.
        app.screen.query_one('#starred', ListView).focus()
        await pilot.press('c')
        await pilot.pause()
        assert isinstance(app.screen, CloneScreen)
        await pilot.press('s')
        await pilot.pause()
        assert copied == ['git@bitbucket.org:acme/widgets.git']
        assert not isinstance(app.screen, CloneScreen)

        # A pull request on the dashboard: its repository is fetched for the links.
        app.screen.query_one('#mine', ListView).focus()
        await pilot.press('c')
        await settle(app, pilot)
        assert ('repository', 'acme', 'widgets') in api.calls
        await pilot.press('h')
        await pilot.pause()
        assert copied[-1] == 'https://ada@bitbucket.org/acme/widgets.git'

        # The pull request list: its repository; Esc closes without copying.
        app.push_screen(PullRequestsScreen(await api.repository('acme', 'gadgets')))
        await settle(app, pilot)
        await pilot.press('c')
        await pilot.pause()
        assert isinstance(app.screen, CloneScreen)
        assert app.screen.links['ssh'] == 'git@bitbucket.org:acme/gadgets.git'
        await pilot.press('escape')
        await pilot.pause()
        assert isinstance(app.screen, PullRequestsScreen) and len(copied) == 2


async def test_url_hotkey_everywhere(monkeypatch):
    api = FakeAPI(me='Ada')
    app = make_app(api)
    copied, opened = [], []
    async with app.run_test(size=(160, 45)) as pilot:
        app.copy_to_clipboard = copied.append
        # No clipboard tool here, so copying falls back to the terminal (OSC 52).
        monkeypatch.setattr('bbtui.screens.url.copy_with_tool', lambda text: None)
        app.open_url = lambda url, **kwargs: opened.append(url)
        await settle(app, pilot)
        # Dashboard: the highlighted item of the focused list.
        app.screen.query_one('#mine', ListView).focus()
        assert await show_url(app, pilot) == 'https://bitbucket.org/acme/widgets/pull-requests/21'
        app.screen.query_one('#starred', ListView).focus()
        assert await show_url(app, pilot) == 'https://bitbucket.org/acme/widgets'

        # Pull request list: the highlighted pull request; `y` copies.
        app.push_screen(PullRequestsScreen(Repository.from_api(repository_json('widgets'))))
        await settle(app, pilot)
        await pilot.press('u')
        await pilot.pause()
        await pilot.press('y')
        await pilot.pause()
        assert copied == ['https://bitbucket.org/acme/widgets/pull-requests/11']
        assert not isinstance(app.screen, UrlScreen)

        # Pull request: `o` in the dialog opens it.
        await pilot.press('enter')
        await settle(app, pilot)
        await pilot.press('u')
        await pilot.pause()
        await pilot.press('o')
        await pilot.pause()
        assert opened == ['https://bitbucket.org/acme/widgets/pull-requests/11']

        # Pipeline run.
        app.push_screen(PipelineRunScreen('acme', 'widgets', 42))
        await settle(app, pilot)
        assert (
            await show_url(app, pilot) == 'https://bitbucket.org/acme/widgets/pipelines/results/42'
        )

        # Pipelines list: the highlighted run.
        app.push_screen(PipelinesScreen(Repository.from_api(repository_json('widgets'))))
        await settle(app, pilot)
        assert (
            await show_url(app, pilot) == 'https://bitbucket.org/acme/widgets/pipelines/results/43'
        )

        # `o` no longer opens anything outside the URL dialog.
        opened.clear()
        await pilot.press('o')
        await pilot.pause()
        assert opened == []


async def test_vim_keys():
    api = FakeAPI(me='Ada')
    app = make_app(api)
    async with app.run_test(size=(160, 24)) as pilot:
        await settle(app, pilot)
        dashboard = app.screen
        starred = dashboard.query_one('#starred', ListView)
        others = dashboard.query_one('#others', ListView)
        mine = dashboard.query_one('#mine', ListView)
        nightly = dashboard.query_one('#nightly', ListView)

        # j/k move within a list.
        others.focus()
        await pilot.press('j')
        assert others.index == 0  # Only one item; stays put.
        mine.focus()
        await pilot.press('j')
        assert mine.index == 1
        await pilot.press('k')
        assert mine.index == 0

        # h/l switch columns, returning to the list you were last in.
        others.focus()
        await pilot.press('l')
        assert app.focused is mine
        nightly.focus()
        await pilot.press('h')
        assert app.focused is others
        await pilot.press('l')
        assert app.focused is nightly
        starred.focus()
        await pilot.press('l', 'h')
        assert app.focused is starred

        # Typing in the search box is unaffected.
        await pilot.press('slash', *'hjkl')
        assert dashboard.query_one('#search', Input).value == 'hjkl'

        # Tables: j/k move the cursor.
        app.push_screen(PullRequestsScreen(Repository.from_api(repository_json('widgets'))))
        await settle(app, pilot)
        table = app.screen.query_one(DataTable)
        await pilot.press('j')
        assert table.cursor_row == 1
        await pilot.press('k')
        assert table.cursor_row == 0

        # Pull request overview: j scrolls; the merge dialog's strategies follow j/k.
        await pilot.press('enter')
        await settle(app, pilot)
        detail = app.screen
        overview = detail.query_one('#overview-scroll')
        overview.focus()
        assert overview.max_scroll_y > 0  # The overview overflows at this size.
        await pilot.press(*'jjjjj')
        await pilot.pause()
        assert overview.scroll_y > 0
        await pilot.press('m')
        await settle(app, pilot)
        dialog = app.screen
        assert isinstance(dialog, MergeScreen)
        dialog.query_one(RadioSet).focus()
        await pilot.press('j', 'j', 'enter')  # The highlight starts on the first strategy.
        await pilot.pause()
        assert dialog.strategy == 'fast_forward'
        await pilot.press('k', 'k', 'enter')
        await pilot.pause()
        assert dialog.strategy == 'merge_commit'


async def test_idle_timeout_returns_to_the_dashboard_unless_editing():
    api = FakeAPI()
    app = make_app(api)
    app.settings.idle_timeout_minutes = 5
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)

        async def idle(minutes: float) -> None:
            app.last_interaction = time.monotonic() - minutes * 60
            await app.check_idle_timeout()
            await pilot.pause()

        await idle(4)
        assert app.screen is detail  # Not idle long enough.
        # A key press counts as use.
        app.last_interaction = time.monotonic() - 3600
        await pilot.press('u')
        await pilot.pause()
        assert time.monotonic() - app.last_interaction < 5
        await app.check_idle_timeout()
        assert isinstance(app.screen, UrlScreen)

        # Writing a comment, or holding an unposted draft, blocks the timeout.
        await pilot.press('escape', 'c')
        await pilot.pause()
        assert isinstance(app.screen, CommentComposer)
        app.screen.query_one(TextArea).insert('half done')
        await idle(60)
        assert isinstance(app.screen, CommentComposer)
        await pilot.press('escape')
        await pilot.pause()
        assert detail.drafts
        await idle(60)
        assert app.screen is detail

        # Otherwise everything above the dashboard closes, modals included.
        detail.drafts.clear()
        await pilot.press('u')
        await pilot.pause()
        await idle(6)
        assert isinstance(app.screen, DashboardScreen)
        assert len(app.screen_stack) == 2
        await idle(60)  # Already on the dashboard: nothing to do.
        assert isinstance(app.screen, DashboardScreen)


async def test_idle_timeout_is_off_by_default():
    app = make_app(FakeAPI())
    assert app.settings.idle_timeout_minutes == 0
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)
        app.last_interaction = time.monotonic() - 10_000
        await app.check_idle_timeout()
        await pilot.pause()
        assert app.screen is detail


async def test_diff_cursor_redraws_only_its_lines():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(120, 40)) as pilot:
        detail = await open_detail(app, pilot)
        await pilot.press('2')
        await settle(app, pilot)
        await pilot.press('enter')
        await pilot.pause()
        view = detail.query_one(DiffView)
        lines = view.query(DiffLines).first()

        def reversed_(y: int) -> bool:
            return any(s.style and s.style.reverse for s in lines.render_line(y))

        cursor = view.cursor - lines.start
        assert reversed_(cursor) and not reversed_(cursor + 1)
        refreshed = []
        original = lines.refresh
        lines.refresh = lambda *regions, **kw: (refreshed.extend(regions), original(*regions, **kw))
        await pilot.press('j')
        assert not reversed_(cursor) and reversed_(cursor + 1)
        assert [region.y for region in refreshed] == [cursor, cursor + 1]
        assert all(region.height == 1 for region in refreshed)


async def test_commits_browse_branches_and_open_a_commit():
    api = FakeAPI()
    app = make_app(api)
    repo = Repository.from_api(repository_json('widgets'))
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        app.push_screen(PullRequestsScreen(repo))
        await settle(app, pilot)
        await pilot.press('C')
        await settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, CommitsScreen)
        table = screen.query_one(DataTable)
        # The main branch's history, a page at a time; nearing the end fetches more.
        assert screen.branch == 'develop' and table.row_count == 30
        # Tagged commits are labelled as `git log --decorate` does.
        assert str(table.get_row_at(1)[3]) == '(tag: v1.0, tag: v1.0-final) Change 68 on develop'
        assert str(table.get_row_at(0)[3]) == 'Change 69 on develop'
        assert 'more as you scroll' in str(table.border_subtitle)
        table.move_cursor(row=27)
        await settle(app, pilot)
        assert table.row_count == 60
        assert ('commits', 'develop', None, 'page:30') in api.calls

        # Only the commits not on another branch; Enter on an empty filter shows all again.
        await pilot.press('x')
        await settle(app, pilot)
        assert isinstance(app.screen, BranchChoiceScreen)
        app.screen.query_one(Input).value = 'ABC-8-old-thing'
        await pilot.press('enter')
        await settle(app, pilot)
        assert app.screen is screen and screen.base == 'ABC-8-old-thing'
        assert table.row_count == 3 and 'not on ABC-8-old-thing' in str(table.border_title)
        await pilot.press('x')
        await settle(app, pilot)
        await pilot.press('enter')
        await settle(app, pilot)
        assert screen.base is None and table.row_count == 30

        # Tags can be picked too, and are listed with branches.
        await pilot.press('b')
        await settle(app, pilot)
        app.screen.query_one(Input).value = 'v1'
        await pilot.pause(0.4)
        await settle(app, pilot)
        options = app.screen.query_one(OptionList)
        assert [str(options.get_option_at_index(i).prompt) for i in range(2)] == [
            'v1.0  tag',
            'v1.0-final  tag',
        ]
        await pilot.press('enter')
        await settle(app, pilot)
        assert screen.branch == 'v1.0'
        assert ('refs', 'v1') in api.calls
        assert api.calls.count(('tags',)) == 1  # Tags are fetched once, not per branch.

        # Another branch; a missing one says so.
        await pilot.press('b')
        await settle(app, pilot)
        app.screen.query_one(Input).value = 'ABC-9-new-thing'
        await pilot.press('enter')
        await settle(app, pilot)
        assert screen.branch == 'ABC-9-new-thing'
        assert str(table.get_row_at(0)[3]) == 'Change 69 on ABC-9-new-thing'

        # A commit: its message, files and diff.
        await pilot.press('enter')
        await pilot.pause(0.2)
        await settle(app, pilot)
        commit = app.screen
        assert isinstance(commit, CommitScreen)
        assert 'Why 69' in str(commit.query_one('#commit-message Static', Static).render())
        chooser = commit.query_one('#file-chooser', DataTable)
        assert chooser.row_count == 2 and app.focused is chooser
        view = commit.query_one(DiffView)
        assert '+new' in ''.join(lines.plain for lines in view.query(DiffLines))
        await pilot.press('right_square_bracket')
        await pilot.pause(0.2)
        await settle(app, pilot)
        assert 'b-new' in ''.join(lines.plain for lines in view.query(DiffLines))
        await pilot.press('u')
        await pilot.pause()
        assert app.screen.url == ('https://bitbucket.org/acme/widgets/commits/ABC69' + 'f' * 34)


async def test_commits_of_a_missing_branch():
    api = FakeAPI()
    app = make_app(api)
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        repo = Repository.from_api(repository_json('widgets'))
        app.push_screen(CommitsScreen(repo, branch='nope'))
        await settle(app, pilot)
        assert app.screen.query_one(DataTable).row_count == 0
        assert any("No branch or tag 'nope'" in str(n.message) for n in app._notifications)


async def test_a_tagged_commit_shows_its_tags():
    app = make_app(FakeAPI())
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        app.push_screen(CommitsScreen(Repository.from_api(repository_json('widgets'))))
        await settle(app, pilot)
        table = app.screen.query_one(DataTable)
        table.move_cursor(row=1)
        await pilot.press('enter')
        await settle(app, pilot)
        header = str(app.screen.query_one('#commit-message Static', Static).render())
        assert header.startswith('(tag: v1.0, tag: v1.0-final) Change 68 on develop')
