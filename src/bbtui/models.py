"""Typed views of Bitbucket Cloud REST v2 resources."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _get(data: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return default
        data = data.get(key)
    return default if data is None else data


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class User:
    display_name: str
    account_id: str | None = None
    nickname: str | None = None
    uuid: str | None = None

    @classmethod
    def from_api(cls, data: dict | None) -> 'User':
        data = data or {}
        return cls(
            display_name=data.get('display_name') or data.get('nickname') or 'unknown',
            account_id=data.get('account_id'),
            nickname=data.get('nickname'),
            uuid=data.get('uuid'),
        )


def same_user(a: User, b: User) -> bool:
    """Whether two user references are the same person (by account id or UUID)."""
    return bool((a.account_id and a.account_id == b.account_id) or (a.uuid and a.uuid == b.uuid))


@dataclass(frozen=True)
class Workspace:
    slug: str
    name: str
    is_admin: bool = False

    @classmethod
    def from_access(cls, data: dict) -> 'Workspace':
        """From a `/user/workspaces` entry: `{administrator, workspace: {slug, name}}`."""
        workspace = data.get('workspace') or {}
        return cls(
            slug=workspace.get('slug', ''),
            name=workspace.get('name') or workspace.get('slug', ''),
            is_admin=bool(data.get('administrator')),
        )


@dataclass(frozen=True)
class Repository:
    full_name: str
    name: str
    slug: str
    description: str = ''
    is_private: bool = True
    updated_on: datetime | None = None
    main_branch: str | None = None
    html_url: str | None = None
    clone_links: tuple[tuple[str, str], ...] = ()
    """`(name, href)` pairs from the API: `https` (with your username) and `ssh`."""

    @property
    def workspace(self) -> str:
        return self.full_name.split('/', 1)[0]

    @property
    def clone_https(self) -> str:
        return dict(self.clone_links).get('https') or f'https://bitbucket.org/{self.full_name}.git'

    @property
    def clone_ssh(self) -> str:
        return dict(self.clone_links).get('ssh') or f'git@bitbucket.org:{self.full_name}.git'

    @classmethod
    def from_api(cls, data: dict) -> 'Repository':
        full_name = data.get('full_name', '')
        return cls(
            full_name=full_name,
            name=data.get('name') or full_name,
            slug=data.get('slug') or full_name.split('/')[-1],
            description=data.get('description') or '',
            is_private=bool(data.get('is_private', True)),
            updated_on=_datetime(data.get('updated_on')),
            main_branch=_get(data, 'mainbranch', 'name'),
            html_url=_get(data, 'links', 'html', 'href'),
            clone_links=tuple(
                (link['name'], link['href'])
                for link in _get(data, 'links', 'clone', default=[])
                if link.get('name') and link.get('href')
            ),
        )


@dataclass(frozen=True)
class Participant:
    user: User
    role: str
    """`REVIEWER` or `PARTICIPANT`."""
    approved: bool
    state: str | None
    """`approved`, `changes_requested` or None."""

    @classmethod
    def from_api(cls, data: dict) -> 'Participant':
        return cls(
            user=User.from_api(data.get('user')),
            role=data.get('role') or 'PARTICIPANT',
            approved=bool(data.get('approved')),
            state=data.get('state'),
        )


@dataclass(frozen=True)
class PullRequest:
    id: int
    title: str
    state: str
    author: User
    source_branch: str
    destination_branch: str
    repository: str
    """Destination repository full name, `workspace/slug`."""
    description: str = ''
    created_on: datetime | None = None
    updated_on: datetime | None = None
    comment_count: int = 0
    task_count: int = 0
    draft: bool = False
    participants: tuple[Participant, ...] = field(default_factory=tuple)
    html_url: str | None = None
    close_source_branch: bool = False
    merge_commit: str | None = None

    @property
    def url(self) -> str:
        """The pull request's page on bitbucket.org."""
        return self.html_url or f'https://bitbucket.org/{self.repository}/pull-requests/{self.id}'

    @property
    def reviewers(self) -> list[Participant]:
        return [p for p in self.participants if p.role == 'REVIEWER']

    @property
    def approvals(self) -> int:
        return sum(1 for p in self.participants if p.approved)

    @property
    def changes_requested(self) -> int:
        return sum(1 for p in self.participants if p.state == 'changes_requested')

    @classmethod
    def from_api(cls, data: dict) -> 'PullRequest':
        return cls(
            id=int(data['id']),
            title=data.get('title') or '',
            state=data.get('state') or '',
            author=User.from_api(data.get('author')),
            source_branch=_get(data, 'source', 'branch', 'name', default=''),
            destination_branch=_get(data, 'destination', 'branch', 'name', default=''),
            repository=_get(data, 'destination', 'repository', 'full_name', default=''),
            description=data.get('description') or '',
            created_on=_datetime(data.get('created_on')),
            updated_on=_datetime(data.get('updated_on')),
            comment_count=data.get('comment_count') or 0,
            task_count=data.get('task_count') or 0,
            draft=bool(data.get('draft')),
            participants=tuple(Participant.from_api(p) for p in data.get('participants') or []),
            html_url=_get(data, 'links', 'html', 'href'),
            close_source_branch=bool(data.get('close_source_branch')),
            merge_commit=_get(data, 'merge_commit', 'hash'),
        )


@dataclass(frozen=True)
class Comment:
    id: int
    author: User
    body: str
    created_on: datetime | None = None
    deleted: bool = False
    parent_id: int | None = None
    path: str | None = None
    """File path for inline comments."""
    line_to: int | None = None
    """New-file line number of an inline comment."""
    line_from: int | None = None
    """Old-file line number of an inline comment on a removed line."""
    outdated: bool = False
    """An inline comment whose line has changed since it was made."""
    context: str = ''
    """For inline comments, the diff lines around the commented line when it was made."""
    resolved_by: User | None = None
    resolved_on: datetime | None = None

    @property
    def resolved(self) -> bool:
        return self.resolved_by is not None

    @property
    def is_inline(self) -> bool:
        return self.path is not None

    @property
    def line(self) -> int | None:
        return self.line_to or self.line_from

    @classmethod
    def from_api(cls, data: dict) -> 'Comment':
        inline = data.get('inline') or {}
        # The comments list gives an empty `resolution` for a resolved thread unless its fields
        # are asked for, so the key's presence is what marks it resolved.
        resolution = data.get('resolution')
        return cls(
            id=int(data['id']),
            author=User.from_api(data.get('user')),
            body=_get(data, 'content', 'raw', default=''),
            created_on=_datetime(data.get('created_on')),
            deleted=bool(data.get('deleted')),
            parent_id=_get(data, 'parent', 'id'),
            path=inline.get('path'),
            line_to=inline.get('to'),
            line_from=inline.get('from'),
            outdated=bool(inline.get('outdated')),
            context=inline.get('context_lines') or '',
            resolved_by=None if resolution is None else User.from_api(resolution.get('user')),
            resolved_on=_datetime((resolution or {}).get('created_on')),
        )


@dataclass(frozen=True)
class DiffStat:
    status: str
    """`added`, `removed`, `modified`, `renamed`, `merge conflict` or `remote deleted`."""
    old_path: str | None
    new_path: str | None
    lines_added: int = 0
    lines_removed: int = 0

    @property
    def path(self) -> str:
        if self.status == 'renamed' and self.old_path and self.new_path:
            return f'{self.old_path} → {self.new_path}'
        return self.new_path or self.old_path or ''

    @property
    def is_conflicted(self) -> bool:
        return self.status in ('merge conflict', 'local deleted', 'remote deleted')

    @property
    def status_letter(self) -> str:
        return {'added': 'A', 'removed': 'D', 'renamed': 'R', 'modified': 'M'}.get(self.status, '!')

    @classmethod
    def from_api(cls, data: dict) -> 'DiffStat':
        return cls(
            status=data.get('status') or 'modified',
            old_path=_get(data, 'old', 'path'),
            new_path=_get(data, 'new', 'path'),
            lines_added=data.get('lines_added') or 0,
            lines_removed=data.get('lines_removed') or 0,
        )


@dataclass(frozen=True)
class BuildStatus:
    """A commit build status, as reported by Bitbucket Pipelines or another CI."""

    key: str
    name: str
    state: str
    """`SUCCESSFUL`, `FAILED`, `INPROGRESS` or `STOPPED`."""
    url: str | None = None
    refname: str | None = None
    updated_on: datetime | None = None

    @classmethod
    def from_api(cls, data: dict) -> 'BuildStatus':
        return cls(
            key=data.get('key') or '',
            name=data.get('name') or data.get('key') or 'build',
            state=data.get('state') or '',
            url=data.get('url'),
            refname=data.get('refname'),
            updated_on=_datetime(data.get('updated_on')),
        )


@dataclass(frozen=True)
class Branch:
    """A branch, or a tag (`is_tag`): both are refs to a commit."""

    name: str
    updated_on: datetime | None = None
    is_tag: bool = False
    target: str = ''
    """The hash of the commit the ref points to."""

    @classmethod
    def from_api(cls, data: dict) -> 'Branch':
        return cls(
            name=data.get('name') or '',
            updated_on=_datetime(_get(data, 'target', 'date')),
            is_tag=data.get('type') == 'tag',
            target=_get(data, 'target', 'hash', default=''),
        )


@dataclass(frozen=True)
class Commit:
    hash: str
    message: str
    author: str = ''
    date: datetime | None = None
    parents: tuple[str, ...] = ()

    @property
    def short_hash(self) -> str:
        return self.hash[:8]

    @property
    def summary(self) -> str:
        return self.message.strip().split('\n', 1)[0] if self.message.strip() else ''

    @classmethod
    def from_api(cls, data: dict) -> 'Commit':
        author = _get(data, 'author', 'user', 'display_name') or _get(data, 'author', 'raw') or ''
        return cls(
            hash=data.get('hash') or '',
            message=data.get('message') or '',
            author=author,
            date=_datetime(data.get('date')),
            parents=tuple(p['hash'] for p in data.get('parents') or [] if p.get('hash')),
        )


def _strip_links(data):
    """A copy of an API object without `links`, for sending it back (e.g. a pipeline target)."""
    if isinstance(data, dict):
        return {k: _strip_links(v) for k, v in data.items() if k != 'links'}
    if isinstance(data, list):
        return [_strip_links(v) for v in data]
    return data


def _pipeline_status(state: dict | None) -> str:
    """One status for a pipeline or step state: the result when completed (`SUCCESSFUL`,
    `FAILED`, `ERROR`, `STOPPED`, `EXPIRED`), else `PENDING`, `RUNNING`, `PAUSED`, ..."""
    state = state or {}
    if result := (state.get('result') or {}).get('name'):
        return result
    if stage := (state.get('stage') or {}).get('name'):
        return stage
    return {'IN_PROGRESS': 'RUNNING'}.get(state.get('name') or '', state.get('name') or '')


RUNNING_STATUSES = ('PENDING', 'RUNNING', 'IN_PROGRESS', 'PAUSED', 'HALTED')


@dataclass(frozen=True)
class PipelineStep:
    uuid: str
    name: str
    status: str
    started_on: datetime | None = None
    completed_on: datetime | None = None
    duration_seconds: int | None = None

    @property
    def is_running(self) -> bool:
        return self.status in RUNNING_STATUSES

    @classmethod
    def from_api(cls, data: dict) -> 'PipelineStep':
        return cls(
            uuid=data.get('uuid') or '',
            name=data.get('name') or 'step',
            status=_pipeline_status(data.get('state')),
            started_on=_datetime(data.get('started_on')),
            completed_on=_datetime(data.get('completed_on')),
            duration_seconds=data.get('duration_in_seconds'),
        )


@dataclass(frozen=True)
class Pipeline:
    uuid: str
    build_number: int
    status: str
    trigger: str = ''
    """`PUSH`, `MANUAL` or `SCHEDULE`."""
    creator: User | None = None
    created_on: datetime | None = None
    completed_on: datetime | None = None
    duration_seconds: int | None = None
    ref_name: str | None = None
    """The branch or tag built, for branch and custom pipelines."""
    selector_type: str = ''
    """`branches`, `tags`, `pull-requests`, `custom` or `default`."""
    selector_pattern: str = ''
    source_branch: str | None = None
    """For pull request pipelines."""
    destination_branch: str | None = None
    commit: str | None = None
    target: dict = field(default_factory=dict)
    """The raw target, without links; posting it again re-runs the pipeline."""

    @property
    def is_running(self) -> bool:
        return self.status in RUNNING_STATUSES

    @property
    def description(self) -> str:
        """What was built: `PR feature → master`, `custom: nightly on master`, `master`."""
        if self.selector_type == 'pull-requests':
            return f'PR {self.source_branch or "?"} → {self.destination_branch or "?"}'
        if self.selector_type == 'custom':
            return f'custom: {self.selector_pattern} on {self.ref_name or "?"}'
        return self.ref_name or self.selector_pattern or ''

    @classmethod
    def from_api(cls, data: dict) -> 'Pipeline':
        target = data.get('target') or {}
        selector = target.get('selector') or {}
        return cls(
            uuid=data.get('uuid') or '',
            build_number=int(data.get('build_number') or 0),
            status=_pipeline_status(data.get('state')),
            trigger=(data.get('trigger') or {}).get('name') or '',
            creator=User.from_api(data['creator']) if data.get('creator') else None,
            created_on=_datetime(data.get('created_on')),
            completed_on=_datetime(data.get('completed_on')),
            duration_seconds=data.get('duration_in_seconds'),
            ref_name=target.get('ref_name'),
            selector_type=selector.get('type') or '',
            selector_pattern=selector.get('pattern') or '',
            source_branch=target.get('source'),
            destination_branch=target.get('destination'),
            commit=_get(target, 'commit', 'hash'),
            target=_strip_links(target),
        )


@dataclass(frozen=True)
class Schedule:
    uuid: str
    enabled: bool
    cron: str
    ref_name: str
    selector_type: str
    selector_pattern: str

    @property
    def name(self) -> str:
        return self.selector_pattern or self.ref_name

    @classmethod
    def from_api(cls, data: dict) -> 'Schedule':
        target = data.get('target') or {}
        selector = target.get('selector') or {}
        return cls(
            uuid=data.get('uuid') or '',
            enabled=bool(data.get('enabled')),
            cron=data.get('cron_pattern') or '',
            ref_name=target.get('ref_name') or '',
            selector_type=selector.get('type') or '',
            selector_pattern=selector.get('pattern') or '',
        )
