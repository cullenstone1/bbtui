"""Bitbucket Cloud endpoints, returning typed models."""

import asyncio
from collections.abc import Sequence

from bbtui.api.client import BitbucketClient, BitbucketError, NotFoundError
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
    Workspace,
)

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

    async def pull_requests_by(
        self, workspace: str, user_uuid: str, state: str = 'OPEN', limit: int = 50
    ) -> list[PullRequest]:
        """Pull requests authored by a user, across every repository in the workspace,
        most recently updated first."""
        values = await self.client.get_all(
            f'/workspaces/{workspace}/pullrequests/{user_uuid}',
            {
                'state': state,
                'sort': '-updated_on',
                'pagelen': min(limit, 50),
                'fields': '+values.participants',
            },
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

    async def pull_request_statuses(
        self, workspace: str, repo_slug: str, pr_id: int
    ) -> list[BuildStatus]:
        """Build statuses (pipelines and other CI) for the pull request's commits."""
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/statuses',
            {'pagelen': MAX_PAGELEN, 'sort': '-updated_on'},
            limit=200,
        )
        return [BuildStatus.from_api(v) for v in values]

    async def pull_request_open_task_count(self, workspace: str, repo_slug: str, pr_id: int) -> int:
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/tasks',
            {'pagelen': MAX_PAGELEN},
            limit=1000,
        )
        return sum(1 for v in values if v.get('state') == 'UNRESOLVED')

    # --- reviewing --------------------------------------------------------------------------------

    def _pr_path(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        return f'/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}'

    async def approve(self, workspace: str, repo_slug: str, pr_id: int) -> None:
        await self.client.send('POST', f'{self._pr_path(workspace, repo_slug, pr_id)}/approve')

    async def unapprove(self, workspace: str, repo_slug: str, pr_id: int) -> None:
        await self.client.send('DELETE', f'{self._pr_path(workspace, repo_slug, pr_id)}/approve')

    async def request_changes(self, workspace: str, repo_slug: str, pr_id: int) -> None:
        path = f'{self._pr_path(workspace, repo_slug, pr_id)}/request-changes'
        await self.client.send('POST', path)

    async def remove_request_changes(self, workspace: str, repo_slug: str, pr_id: int) -> None:
        path = f'{self._pr_path(workspace, repo_slug, pr_id)}/request-changes'
        await self.client.send('DELETE', path)

    async def create_comment(
        self,
        workspace: str,
        repo_slug: str,
        pr_id: int,
        body: str,
        *,
        path: str | None = None,
        line_to: int | None = None,
        line_from: int | None = None,
        parent_id: int | None = None,
    ) -> Comment:
        """Post a comment: general, inline (`path` plus `line_to` for new/context lines or
        `line_from` for removed lines), or a reply (`parent_id`; replies inherit the parent's
        location)."""
        payload: dict = {'content': {'raw': body}}
        if parent_id is not None:
            payload['parent'] = {'id': parent_id}
        elif path is not None:
            inline: dict = {'path': path}
            if line_to is not None:
                inline['to'] = line_to
            elif line_from is not None:
                inline['from'] = line_from
            payload['inline'] = inline
        data = await self.client.send(
            'POST', f'{self._pr_path(workspace, repo_slug, pr_id)}/comments', payload
        )
        return Comment.from_api(data or {'id': 0})

    # --- creating pull requests -----------------------------------------------------------------

    async def branches(
        self, workspace: str, repo_slug: str, search: str = '', limit: int = 30
    ) -> list[Branch]:
        """Branches, most recently committed first, optionally filtered by name."""
        params: dict = {'sort': '-target.date', 'pagelen': min(limit, MAX_PAGELEN)}
        if search:
            params['q'] = f'name ~ {_bbql_string(search)}'
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/refs/branches', params, limit=limit
        )
        return [Branch.from_api(v) for v in values]

    async def branch_exists(self, workspace: str, repo_slug: str, name: str) -> bool:
        try:
            await self.client.get_json(
                f'/repositories/{workspace}/{repo_slug}/refs/branches/{name}'
            )
        except NotFoundError:
            return False
        return True

    async def development_branch(self, workspace: str, repo_slug: str) -> str | None:
        """The branch pull requests normally target: the branching model's development branch,
        else the repository's main branch."""
        try:
            model = await self.client.get_json(
                f'/repositories/{workspace}/{repo_slug}/branching-model'
            )
        except BitbucketError:
            model = {}
        if name := (model.get('development') or {}).get('name'):
            return name
        return (await self.repository(workspace, repo_slug)).main_branch

    async def commits_between(
        self, workspace: str, repo_slug: str, source: str, destination: str, limit: int = 100
    ) -> list[Commit]:
        """Commits on `source` that aren't on `destination`, newest first."""
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/commits',
            {'include': source, 'exclude': destination, 'pagelen': min(limit, MAX_PAGELEN)},
            limit=limit,
        )
        return [Commit.from_api(v) for v in values]

    async def diffstat_between(
        self, workspace: str, repo_slug: str, source: str, destination: str
    ) -> list[DiffStat]:
        """Files changed on `source` since it diverged from `destination`."""
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/diffstat/{source}..{destination}',
            {'pagelen': MAX_PAGELEN},
            limit=1000,
        )
        return [DiffStat.from_api(v) for v in values]

    async def default_reviewers(self, workspace: str, repo_slug: str) -> list[User]:
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/effective-default-reviewers',
            {'pagelen': MAX_PAGELEN},
        )
        return [User.from_api(v.get('user')) for v in values]

    async def open_pull_requests_from(
        self, workspace: str, repo_slug: str, source: str
    ) -> list[PullRequest]:
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/pullrequests',
            {'q': f'source.branch.name = {_bbql_string(source)} AND state = "OPEN"'},
            limit=10,
        )
        return [PullRequest.from_api(v) for v in values]

    async def create_pull_request(
        self,
        workspace: str,
        repo_slug: str,
        *,
        title: str,
        source: str,
        destination: str,
        description: str = '',
        reviewer_uuids: Sequence[str] = (),
        close_source_branch: bool = False,
        draft: bool = False,
    ) -> PullRequest:
        payload = {
            'title': title,
            'description': description,
            'source': {'branch': {'name': source}},
            'destination': {'branch': {'name': destination}},
            'reviewers': [{'uuid': uuid} for uuid in reviewer_uuids],
            'close_source_branch': close_source_branch,
            'draft': draft,
        }
        data = await self.client.send(
            'POST', f'/repositories/{workspace}/{repo_slug}/pullrequests', payload
        )
        return PullRequest.from_api(data or {'id': 0})

    # --- pipelines --------------------------------------------------------------------------------

    def _pipelines_path(self, workspace: str, repo_slug: str) -> str:
        return f'/repositories/{workspace}/{repo_slug}/pipelines'

    async def pipelines(self, workspace: str, repo_slug: str, limit: int = 50) -> list[Pipeline]:
        """Pipeline runs, newest first."""
        values = await self.client.get_all(
            self._pipelines_path(workspace, repo_slug),
            {'sort': '-created_on', 'pagelen': min(limit, MAX_PAGELEN)},
            limit=limit,
        )
        return [Pipeline.from_api(v) for v in values]

    async def pipeline(self, workspace: str, repo_slug: str, run: int | str) -> Pipeline:
        """A run by build number or UUID."""
        path = f'{self._pipelines_path(workspace, repo_slug)}/{run}'
        return Pipeline.from_api(await self.client.get_json(path))

    async def pipeline_steps(
        self, workspace: str, repo_slug: str, pipeline_uuid: str
    ) -> list[PipelineStep]:
        values = await self.client.get_all(
            f'{self._pipelines_path(workspace, repo_slug)}/{pipeline_uuid}/steps',
            {'pagelen': MAX_PAGELEN},
        )
        return [PipelineStep.from_api(v) for v in values]

    async def step_log(
        self, workspace: str, repo_slug: str, pipeline_uuid: str, step_uuid: str, start: int = 0
    ) -> tuple[bytes, int | None]:
        """A step's log from byte `start`, and the log's total size so far."""
        path = f'{self._pipelines_path(workspace, repo_slug)}/{pipeline_uuid}/steps/{step_uuid}/log'
        try:
            return await self.client.get_bytes(path, start)
        except NotFoundError:
            return b'', start  # The step hasn't produced a log yet.

    async def schedules(self, workspace: str, repo_slug: str) -> list[Schedule]:
        values = await self.client.get_all(
            f'/repositories/{workspace}/{repo_slug}/pipelines_config/schedules',
            {'pagelen': MAX_PAGELEN},
        )
        return [Schedule.from_api(v) for v in values]

    async def latest_scheduled_run(
        self, workspace: str, repo_slug: str, schedule: Schedule, search: int = 200
    ) -> Pipeline | None:
        """The newest run started by `schedule`, looking through the last `search` runs."""
        for run in await self.pipelines(workspace, repo_slug, limit=search):
            if (
                run.trigger == 'SCHEDULE'
                and run.selector_type == schedule.selector_type
                and run.selector_pattern == schedule.selector_pattern
                and (run.ref_name or '') == schedule.ref_name
            ):
                return run
        return None

    async def rerun_pipeline(self, workspace: str, repo_slug: str, run: Pipeline) -> Pipeline:
        """Start a new run with the same target (branch, pull request or custom pipeline)."""
        data = await self.client.send(
            'POST', f'{self._pipelines_path(workspace, repo_slug)}/', {'target': run.target}
        )
        return Pipeline.from_api(data or {})

    async def stop_pipeline(self, workspace: str, repo_slug: str, pipeline_uuid: str) -> None:
        path = f'{self._pipelines_path(workspace, repo_slug)}/{pipeline_uuid}/stopPipeline'
        await self.client.send('POST', path)
