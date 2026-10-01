"""Bitbucket API payloads shaped like the real responses."""


def repository_json(slug: str, workspace: str = 'acme', **overrides) -> dict:
    data = {
        'type': 'repository',
        'full_name': f'{workspace}/{slug}',
        'name': slug,
        'slug': slug,
        'description': f'{slug} description',
        'is_private': True,
        'updated_on': '2026-09-30T12:00:00.000000+00:00',
        'mainbranch': {'name': 'develop'},
        'links': {'html': {'href': f'https://bitbucket.org/{workspace}/{slug}'}},
    }
    data.update(overrides)
    return data


def user_json(name: str) -> dict:
    return {'display_name': name, 'account_id': f'id-{name.lower()}', 'nickname': name.lower()}


def pull_request_json(pr_id: int, repo: str = 'acme/widgets', **overrides) -> dict:
    data = {
        'id': pr_id,
        'title': f'PR {pr_id}',
        'description': 'Some **markdown**',
        'state': 'OPEN',
        'draft': False,
        'author': user_json('Ada'),
        'source': {'branch': {'name': f'feature/{pr_id}'}},
        'destination': {'branch': {'name': 'develop'}, 'repository': {'full_name': repo}},
        'created_on': '2026-09-29T10:00:00.000000+00:00',
        'updated_on': '2026-09-30T10:00:00.000000+00:00',
        'comment_count': 2,
        'task_count': 0,
        'participants': [
            {'user': user_json('Bob'), 'role': 'REVIEWER', 'approved': True, 'state': 'approved'},
            {
                'user': user_json('Cy'),
                'role': 'REVIEWER',
                'approved': False,
                'state': 'changes_requested',
            },
            {'user': user_json('Di'), 'role': 'PARTICIPANT', 'approved': False, 'state': None},
        ],
        'links': {'html': {'href': f'https://bitbucket.org/{repo}/pull-requests/{pr_id}'}},
    }
    data.update(overrides)
    return data


def comment_json(comment_id: int, body: str, parent: int | None = None, **overrides) -> dict:
    data = {
        'id': comment_id,
        'content': {'raw': body},
        'user': user_json('Bob'),
        'created_on': f'2026-09-30T10:{comment_id:02d}:00.000000+00:00',
        'deleted': False,
    }
    if parent is not None:
        data['parent'] = {'id': parent}
    data.update(overrides)
    return data


def diffstat_json(status: str, old: str | None, new: str | None, added=1, removed=0) -> dict:
    return {
        'status': status,
        'old': {'path': old} if old else None,
        'new': {'path': new} if new else None,
        'lines_added': added,
        'lines_removed': removed,
    }
