"""Bitbucket Cloud endpoints, returning typed models."""

import asyncio
from collections.abc import Sequence

from bbtui.api.client import BitbucketClient, BitbucketError
from bbtui.models import Comment, DiffStat, PullRequest, Repository, User, Workspace

MAX_PAGELEN = 100
"""The largest `pagelen` most Bitbucket collections accept."""


def _bbql_string(value: str) -> str:
    """Quote a value for a BBQL `q` filter."""
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def split_repo_name(name: str, default_workspace: str) -> tuple[str, str]:
    """`repo` or `workspace/repo` → `(workspace, repo)`."""
    workspace, _, slug = name.strip().rpartition('/')
    return (workspace or default_workspace, slug)


class BitbucketAPI:
    def __init__(self, client: BitbucketClient):
        self.client = client

    async def aclose(self) -> None:
        await self.client.aclose()

    # --- user and workspaces --------------------------------------------------------------------

    async def current_user(self) -> User:
        return User.from_api(await self.client.get_json('/user'))

    async def workspaces(self) -> list[Workspace]:
        # `/workspaces` was removed; `/user/workspaces` is its replacement.
        values = await self.client.get_all('/user/workspaces', {'pagelen': MAX_PAGELEN})
        return [Workspace.from_access(v) for v in values]

    # --- repositories ---------------------------------------------------------------------------

    async def repository(self, workspace: str, repo_slug: str) -> Repository:
        return Repository.from_api(
            await self.client.get_json(f'/repositories/{workspace}/{repo_slug}')
        )

    async def repositories(
        self, names: Sequence[str], default_workspace: str
    ) -> tuple[list[Repository], list[str]]:
        """Look up `names` (`repo` or `workspace/repo`) in parallel, keeping their order.

        Returns the repositories found and the names that could not be found.
        """
        targets = [split_repo_name(name, default_workspace) for name in names]
        results = await asyncio.gather(
            *(self.repository(ws, slug) for ws, slug in targets), return_exceptions=True
        )
        found: list[Repository] = []
        missing: list[str] = []
        for name, result in zip(names, results, strict=True):
            if isinstance(result, Repository):
                found.append(result)
            elif isinstance(result, BitbucketError):
                missing.append(name)
            else:
                raise result
        return found, missing

    async def recent_repositories(self, workspace: str, limit: int = 10) -> list[Repository]:
        """Repositories you can contribute to, most recently updated first."""
        page = await self.client.get_json(
            f'/repositories/{workspace}',
            {'role': 'contributor', 'sort': '-updated_on', 'pagelen': min(limit, MAX_PAGELEN)},
        )
        return [Repository.from_api(v) for v in page.get('values') or []][:limit]

    async def search_repositories(
        self, workspace: str, text: str, limit: int = 50
    ) -> list[Repository]:
        """Repositories whose name contains `text`, most recently updated first."""
        values = await self.client.get_all(
            f'/repositories/{workspace}',
            {
                'q': f'name ~ {_bbql_string(text)}',
                'sort': '-updated_on',
                'pagelen': min(limit, MAX_PAGELEN),
            },
            limit=limit,
        )
        return [Repository.from_api(v) for v in values]

    # --- pull requests --------------------------------------------------------------------------

    async def pull_requests(
        self, workspace: str, repo_slug: str, state: str = 'OPEN', limit: int = 50
    ) -> list[PullRequest]:
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/pullrequests',
            # The list omits participants unless asked; reviews need them.
            {'state': state, 'pagelen': min(limit, 50), 'fields': '+values.participants'},
            limit=limit,
        )
        return [PullRequest.from_api(v) for v in values]

    async def pull_request(self, workspace: str, repo_slug: str, pr_id: int) -> PullRequest:
        data = await self.client.get_json(
            f'/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}'
        )
        return PullRequest.from_api(data)

    async def pull_request_diffstat(
        self, workspace: str, repo_slug: str, pr_id: int, limit: int = 1000
    ) -> list[DiffStat]:
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/diffstat',
            {'pagelen': MAX_PAGELEN},
            limit=limit,
        )
        return [DiffStat.from_api(v) for v in values]

    async def pull_request_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        return await self.client.get_text(
            f'/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/diff'
        )

    async def pull_request_comments(
        self, workspace: str, repo_slug: str, pr_id: int, limit: int = 1000
    ) -> list[Comment]:
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/comments',
            {'pagelen': MAX_PAGELEN},
            limit=limit,
        )
        return [Comment.from_api(v) for v in values]
