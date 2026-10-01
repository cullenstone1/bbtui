from bbtui.models import Commit
from bbtui.pull_request_defaults import default_description, default_title, title_from_branch


def commit(message: str) -> Commit:
    return Commit(hash='abc', message=message)


def test_title_from_branch():
    assert (
        title_from_branch('ABC-4281_widget_pause_resume_change')
        == 'ABC-4281 Widget pause resume change'
    )
    assert title_from_branch('feature/ABC-12-add-thing') == 'ABC-12 Add thing'
    assert title_from_branch('bugfix/fix--the_bug') == 'Fix the bug'
    assert title_from_branch('ABC-7') == 'ABC-7'


def test_single_commit_uses_its_summary():
    commits = [commit('ABC-4550 Fix vendored_lib submodule\n\nDetails here')]
    assert default_title('ABC-4550-fix-submodule', commits) == 'ABC-4550 Fix vendored_lib submodule'


def test_several_commits_use_the_branch_and_list_oldest_first():
    commits = [commit('second'), commit('first\n\nbody')]  # newest first, as from the API
    assert default_title('ABC-1-thing', commits) == 'ABC-1 Thing'
    assert default_description(commits) == '* first\n* second'
