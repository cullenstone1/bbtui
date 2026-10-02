"""A pull request's history, from its `/activity` feed."""

from dataclasses import dataclass
from datetime import datetime

from bbtui.models import User, _datetime, _get, same_user

STATUS_CHANGES = {'fulfilled': 'merged', 'rejected': 'declined', 'open': 'reopened'}


@dataclass(frozen=True)
class Event:
    user: User
    date: datetime | None
    action: str
    """What happened, e.g. `approved` or `pushed 1a2b3c4d`; follows the user's name."""
    kind: str = 'update'
    """`opened`, `pushed`, `approved`, `changes`, `merged`, `declined` or `update`; for styling."""
    count: int = 1
    """How many consecutive pushes by the same person this event stands for."""


def _names(users: list | None) -> str:
    return ', '.join(User.from_api(u).display_name for u in users or [])


def _update_events(update: dict, user: User, date: datetime | None) -> list[Event]:
    """Events for an `update` entry's `changes` (status, draft, reviewers, title, description)."""
    changes = update.get('changes') or {}
    events = []
    if status := STATUS_CHANGES.get(_get(changes, 'status', 'new')):
        kind = status if status in ('merged', 'declined') else 'update'
        events.append(Event(user, date, status, kind))
    if 'draft' in changes:
        draft = _get(changes, 'draft', 'new')
        events.append(Event(user, date, 'converted to a draft' if draft else 'marked ready'))
    reviewers = changes.get('reviewers') or {}
    if added := _names(reviewers.get('added')):
        events.append(Event(user, date, f'added reviewers {added}'))
    if removed := _names(reviewers.get('removed')):
        events.append(Event(user, date, f'removed reviewers {removed}'))
    if 'title' in changes:
        events.append(
            Event(user, date, f'retitled to "{_get(changes, "title", "new", default="")}"')
        )
    if 'description' in changes:
        events.append(Event(user, date, 'edited the description'))
    return events


def pull_request_history(activity: list[dict]) -> list[Event]:
    """Activity entries (any order) → events, oldest first.

    Comments are left out (they are shown on their own). The first update is the opening; later
    updates that move the source commit are pushes, and consecutive pushes by the same person
    are merged into one event.
    """

    def date_of(entry: dict) -> str:
        for key in ('update', 'approval', 'changes_request'):
            if key in entry:
                return str(entry[key].get('date') or '')
        return ''

    events: list[Event] = []
    source: str | None = None
    for entry in sorted(activity, key=date_of):
        if 'approval' in entry:
            approval = entry['approval']
            user = User.from_api(approval.get('user'))
            events.append(Event(user, _datetime(approval.get('date')), 'approved', 'approved'))
        elif 'changes_request' in entry:
            request = entry['changes_request']
            user = User.from_api(request.get('user'))
            date = _datetime(request.get('date'))
            events.append(Event(user, date, 'requested changes', 'changes'))
        elif 'update' in entry:
            update = entry['update']
            user = User.from_api(update.get('author'))
            date = _datetime(update.get('date'))
            commit = _get(update, 'source', 'commit', 'hash')
            if source is None:
                draft = ' as a draft' if update.get('draft') else ''
                events.append(Event(user, date, f'opened{draft}', 'opened'))
            elif commit and commit != source:
                last = events[-1] if events else None
                if last and last.kind == 'pushed' and same_user(last.user, user):
                    events[-1] = Event(user, date, f'pushed {commit[:8]}', 'pushed', last.count + 1)
                else:
                    events.append(Event(user, date, f'pushed {commit[:8]}', 'pushed'))
            events.extend(_update_events(update, user, date))
            source = commit or source
    return events
