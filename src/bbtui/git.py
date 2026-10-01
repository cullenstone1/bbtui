"""Reading the local git checkout, to default a new pull request's source branch."""

import asyncio
from pathlib import Path
import re

BITBUCKET_REMOTE = re.compile(
    r'bitbucket\.org[:/](?P<workspace>[^/]+)/(?P<slug>[^/\s]+?)(?:\.git)?/?$'
)


def remote_repository(url: str) -> tuple[str, str] | None:
    """`(workspace, repo_slug)` for a Bitbucket Cloud remote URL (SSH or HTTPS)."""
    match = BITBUCKET_REMOTE.search(url.strip())
    return (match['workspace'].lower(), match['slug'].lower()) if match else None


async def _git(*args: str, cwd: Path | None = None) -> str | None:
    try:
        process = await asyncio.create_subprocess_exec(
            'git',
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    stdout, _ = await process.communicate()
    return stdout.decode(errors='replace').strip() if process.returncode == 0 else None


async def current_branch_for(workspace: str, repo_slug: str, cwd: Path | None = None) -> str | None:
    """The checked-out branch, if `cwd` is a clone of `workspace/repo_slug`."""
    remotes = await _git('remote', '-v', cwd=cwd)
    if not remotes:
        return None
    wanted = (workspace.lower(), repo_slug.lower())
    urls = (line.split()[1] for line in remotes.splitlines() if len(line.split()) >= 2)
    if not any(remote_repository(url) == wanted for url in urls):
        return None
    # `symbolic-ref` also works before the first commit, and fails on a detached HEAD.
    return await _git('symbolic-ref', '--short', '-q', 'HEAD', cwd=cwd) or None
