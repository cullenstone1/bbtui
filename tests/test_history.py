from bbtui.history import pull_request_history
from bbtui.models import Comment
from tests.factories import comment_json, user_json


def update(user, date, commit, **changes):
    return {
        'update': {
            'author': user_json(user),
            'date': f'2026-09-29T{date}+00:00',
            'source': {'commit': {'hash': commit}},
            'changes': changes,
        }
    }


def test_history_reads_updates_oldest_first():
    activity = [
        update('Cy', '15:00:00', 'c4', status={'old': 'open', 'new': 'fulfilled'}),
        {'changes_request': {'user': user_json('Bob'), 'date': '2026-09-29T14:00:00+00:00'}},
        update('Cy', '13:00:00', 'c4', title={'old': 'a', 'new': 'b'}, description={}),
        update('Cy', '12:00:00', 'c4', reviewers={'removed': [user_json('Di')]}),
        update('Ada', '11:00:00', 'c4'),
        update('Cy', '10:30:00', 'c2'),
        update('Ada', '10:00:00', 'c1'),
        {'comment': {'id': 1, 'created_on': '2026-09-29T10:10:00+00:00'}},
    ]
    events = [
        (e.user.display_name, e.action, e.kind, e.count) for e in pull_request_history(activity)
    ]
    assert events == [
        ('Ada', 'opened', 'opened', 1),
        ('Cy', 'pushed c2', 'pushed', 1),  # Different people's pushes aren't merged.
        ('Ada', 'pushed c4', 'pushed', 1),
        ('Cy', 'removed reviewers Di', 'update', 1),
        ('Cy', 'retitled to "b"', 'update', 1),
        ('Cy', 'edited the description', 'update', 1),
        ('Bob', 'requested changes', 'changes', 1),
        ('Cy', 'merged', 'merged', 1),
    ]


def test_comment_outdated_and_resolution():
    inline = {'path': 'a.py', 'to': 3, 'from': None, 'outdated': True, 'context_lines': ' x'}
    comment = Comment.from_api(comment_json(1, 'hi', inline=inline))
    assert comment.outdated and comment.context == ' x' and not comment.resolved

    # Without the resolution fields asked for, Bitbucket sends `{}` for a resolved thread.
    assert Comment.from_api(comment_json(2, 'hi', resolution={})).resolved
    resolved = Comment.from_api(
        comment_json(
            3, 'hi', resolution={'user': user_json('Cy'), 'created_on': '2026-09-30T12:00:00+00:00'}
        )
    )
    assert resolved.resolved_by.display_name == 'Cy' and resolved.resolved_on is not None
