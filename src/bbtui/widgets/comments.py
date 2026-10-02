from collections import defaultdict
import re

from rich.text import Text
from textual.binding import Binding
from textual.widgets import Markdown

from bbtui.models import Comment
from bbtui.text import clean, one_line, timestamp, truncate

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


def context_snippet(context: str, max_lines: int = 7) -> str:
    """The code lines of a comment's diff context, without the file and hunk headers."""
    lines = [
        line
        for line in clean(context).split('\n')
        if line and not line.startswith(('--- ', '+++ ', '@@'))
    ]
    return '\n'.join(lines[-max_lines:])


def fenced(code: str, language: str = 'diff') -> str:
    """`code` as a Markdown code block, with a fence longer than any backtick run inside it."""
    ticks = '`' * max(3, max((len(run) for run in re.findall(r'`+', code)), default=0) + 1)
    return f'{ticks}{language}\n{code}\n{ticks}'


def first_line(text: str, width: int = 100) -> str:
    line = next((line.strip() for line in clean(text).split('\n') if line.strip()), '')
    return truncate(line, width)


class CommentView(Markdown):
    """One comment, rendered as Markdown in a titled border, indented by reply depth.

    Focusable, so the pull request screen can reply to the focused comment. An outdated inline
    comment is labelled and, with `show_context`, shows the code it was made on. A resolved
    thread starts collapsed to its first line, like on the web; Enter on its first comment
    expands or collapses it.
    """

    BINDINGS = [Binding('enter', 'toggle_thread', 'Expand/collapse')]

    can_focus = True

    def __init__(
        self,
        comment: Comment,
        depth: int,
        names: dict[str, str],
        show_location: bool = True,
        show_context: bool | None = None,
    ):
        self.comment = comment
        self.depth = depth
        self.names = names
        self.show_location = show_location
        self.show_context = comment.outdated if show_context is None else show_context
        self.replies: list[CommentView] = []
        self.expanded = not comment.resolved
        super().__init__(self.markdown(), classes='comment', open_links=True)
        title = Text(f'{one_line(comment.author.display_name)} · {timestamp(comment.created_on)}')
        if comment.outdated:
            title.append(' · ')
            title.append('outdated', style='bold yellow')
        self.border_title = title
        self.set_class(comment.resolved, 'resolved')
        self.show_subtitle()

    def markdown(self) -> str:
        comment = self.comment
        if comment.deleted:
            return '_(deleted)_'
        if not self.expanded:
            return first_line(resolve_mentions(comment.body, self.names)) or '_(empty)_'
        body = resolve_mentions(clean(comment.body), self.names)
        if self.show_context and (snippet := context_snippet(comment.context)):
            body = f'{fenced(snippet)}\n\n{body}'
        return body

    def show_subtitle(self) -> None:
        comment = self.comment
        subtitle = Text()
        if self.show_location and comment.is_inline:
            subtitle.append(one_line(comment.path) + (f':{comment.line}' if comment.line else ''))
        if comment.resolved_by is not None:
            if subtitle:
                subtitle.append(' · ')
            subtitle.append(f'✔ resolved by {one_line(comment.resolved_by.display_name)}', 'green')
            hidden = len(self.replies)
            if not self.expanded and hidden:
                subtitle.append(f' · {hidden} {"reply" if hidden == 1 else "replies"} hidden')
            subtitle.append(' · ⏎ ' + ('collapse' if self.expanded else 'expand'))
        self.border_subtitle = subtitle

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == 'toggle_thread':
            return self.comment.resolved
        return True

    async def action_toggle_thread(self) -> None:
        self.expanded = not self.expanded
        for reply in self.replies:
            reply.display = self.expanded
        self.show_subtitle()
        await self.update(self.markdown())

    def on_mount(self) -> None:
        self.styles.margin = (0, 0, 0, 4 * self.depth)


def thread_views(
    thread: Thread,
    names: dict[str, str],
    show_location: bool = True,
    show_context: bool | None = None,
) -> list[CommentView]:
    """Cards for a thread: its first comment, then the replies (hidden while it's collapsed)."""
    views = [
        CommentView(comment, depth, names, show_location, show_context if depth == 0 else False)
        for comment, depth in thread
    ]
    root, replies = views[0], views[1:]
    root.replies = replies
    root.show_subtitle()
    for reply in replies:
        reply.display = root.expanded
    return views
