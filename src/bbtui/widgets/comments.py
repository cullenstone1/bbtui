from collections import defaultdict
import re

from rich.text import Text
from textual.widgets import Markdown

from bbtui.models import Comment
from bbtui.text import clean, one_line, timestamp

MENTION = re.compile(r'@\{([^}]+)\}')

Thread = list[tuple[Comment, int]]
"""A root comment followed by its replies, each with its reply depth."""


def resolve_mentions(text: str, names: dict[str, str]) -> str:
    """Replace Bitbucket's raw `@{account_id}` mentions with `@Display Name` where known."""
    return MENTION.sub(lambda m: f'@{names[m[1]]}' if m[1] in names else m[0], text)


def comment_threads(comments: list[Comment]) -> list[Thread]:
    """Comments grouped into threads, oldest first, replies under their parent.

    Replies whose parent is missing start their own thread.
    """
    ids = {c.id for c in comments}
    children: dict[int | None, list[Comment]] = defaultdict(list)
    for comment in comments:
        children[comment.parent_id if comment.parent_id in ids else None].append(comment)
    for siblings in children.values():
        siblings.sort(key=lambda c: (c.created_on is None, c.created_on))

    def walk(comment: Comment, depth: int, thread: Thread) -> Thread:
        thread.append((comment, depth))
        for reply in children[comment.id]:
            walk(reply, depth + 1, thread)
        return thread

    return [walk(root, 0, []) for root in children[None]]


class CommentView(Markdown):
    """One comment, rendered as Markdown in a titled border, indented by reply depth."""

    def __init__(
        self, comment: Comment, depth: int, names: dict[str, str], show_location: bool = True
    ):
        body = '_(deleted)_' if comment.deleted else resolve_mentions(clean(comment.body), names)
        super().__init__(body, classes='comment', open_links=True)
        self.comment = comment
        self.depth = depth
        self.border_title = Text(
            f'{one_line(comment.author.display_name)} · {timestamp(comment.created_on)}'
        )
        if show_location and comment.is_inline:
            location = one_line(comment.path) + (f':{comment.line}' if comment.line else '')
            self.border_subtitle = Text(location)

    def on_mount(self) -> None:
        self.styles.margin = (0, 0, 0, 4 * self.depth)
