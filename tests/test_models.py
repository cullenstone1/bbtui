from bbtui.models import Comment, DiffStat, PullRequest, Repository, Workspace
from tests.factories import comment_json, diffstat_json, pull_request_json, repository_json


def test_repository_from_api():
    repo = Repository.from_api(repository_json('widgets'))
    assert repo.full_name == 'acme/widgets'
    assert repo.workspace == 'acme'
    assert repo.main_branch == 'develop'
    assert repo.updated_on is not None and repo.updated_on.year == 2026
    # Without clone links from the API, they're built from the name.
    assert repo.clone_https == 'https://bitbucket.org/acme/widgets.git'
    assert repo.clone_ssh == 'git@bitbucket.org:acme/widgets.git'
    links = [
        {'name': 'https', 'href': 'https://ada@bitbucket.org/acme/widgets.git'},
        {'name': 'ssh', 'href': 'git@bitbucket.org:acme/widgets.git'},
    ]
    data = repository_json('widgets')
    repo = Repository.from_api({**data, 'links': {**data['links'], 'clone': links}})
    assert repo.clone_https == 'https://ada@bitbucket.org/acme/widgets.git'


def test_pull_request_from_api():
    pr = PullRequest.from_api(pull_request_json(7))
    assert pr.id == 7
    assert pr.author.display_name == 'Ada'
    assert (pr.source_branch, pr.destination_branch) == ('feature/7', 'develop')
    assert pr.repository == 'acme/widgets'
    assert [r.user.display_name for r in pr.reviewers] == ['Bob', 'Cy']
    assert pr.approvals == 1
    assert pr.changes_requested == 1


def test_pull_request_tolerates_missing_fields():
    pr = PullRequest.from_api({'id': 1})
    assert pr.title == ''
    assert pr.author.display_name == 'unknown'
    assert pr.participants == ()


def test_inline_comment_and_reply():
    inline = Comment.from_api(
        comment_json(1, 'nit', inline={'path': 'src/a.py', 'to': None, 'from': 12})
    )
    assert inline.is_inline
    assert (inline.path, inline.line_from, inline.line) == ('src/a.py', 12, 12)
    reply = Comment.from_api(comment_json(2, 'fixed', parent=1))
    assert reply.parent_id == 1
    assert not reply.is_inline


def test_diffstat_paths():
    renamed = DiffStat.from_api(diffstat_json('renamed', 'old.py', 'new.py'))
    assert renamed.path == 'old.py → new.py'
    assert renamed.status_letter == 'R'
    removed = DiffStat.from_api(diffstat_json('removed', 'gone.py', None))
    assert removed.path == 'gone.py'
    assert removed.status_letter == 'D'
    conflict = DiffStat.from_api(diffstat_json('merge conflict', 'x.py', 'x.py'))
    assert conflict.status_letter == '!'


def test_workspace_from_user_workspaces_entry():
    workspace = Workspace.from_access(
        {'administrator': True, 'workspace': {'slug': 'acme', 'uuid': '{1}'}}
    )
    assert workspace == Workspace(slug='acme', name='acme', is_admin=True)
