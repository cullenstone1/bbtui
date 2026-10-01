import subprocess

from bbtui.git import current_branch_for, remote_repository


def test_remote_repository_parses_ssh_and_https():
    assert remote_repository('git@bitbucket.org:Acme/Widgets.git') == ('acme', 'widgets')
    assert remote_repository('https://me@bitbucket.org/acme/widgets.git') == ('acme', 'widgets')
    assert remote_repository('https://bitbucket.org/acme/widgets') == ('acme', 'widgets')
    assert remote_repository('git@github.com:acme/widgets.git') is None


def git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


async def test_current_branch_only_for_a_clone_of_the_repo(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'remote', 'add', 'origin', 'git@bitbucket.org:acme/widgets.git')
    git(tmp_path, 'checkout', '-q', '-b', 'ABC-1-thing')
    assert await current_branch_for('acme', 'widgets', tmp_path) == 'ABC-1-thing'
    assert await current_branch_for('acme', 'gadgets', tmp_path) is None
    assert await current_branch_for('acme', 'widgets', tmp_path / 'missing') is None
