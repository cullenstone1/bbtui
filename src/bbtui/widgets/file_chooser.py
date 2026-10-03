from rich.text import Text
from textual.widgets import DataTable

from bbtui.models import DiffStat
from bbtui.text import one_line

STATUS_STYLES = {'A': 'green', 'D': 'red', 'R': 'yellow', 'M': 'blue'}


def setup_file_chooser(chooser: DataTable, comments: bool = True) -> None:
    """Columns for a list of changed files: status, path, lines added and removed, and
    optionally a comment count."""
    chooser.border_title = 'Files'
    chooser.add_column('', key='status')
    chooser.add_column('Path', key='path')
    chooser.add_column('+', key='added')
    chooser.add_column('−', key='removed')
    if comments:
        chooser.add_column('💬', key='comments')


def fill_file_chooser(
    chooser: DataTable, diffstat: list[DiffStat], comment_counts: list[int] | None = None
) -> None:
    """Replace the rows with `diffstat` (keyed by index), and sum the changes in the subtitle."""
    chooser.clear()
    for index, stat in enumerate(diffstat):
        letter = stat.status_letter
        cells = [
            Text(letter, style=f'bold {STATUS_STYLES.get(letter, "magenta")}'),
            Text(one_line(stat.path)),
            Text(f'+{stat.lines_added}', style='green'),
            Text(f'−{stat.lines_removed}', style='red'),
        ]
        if comment_counts is not None:
            count = comment_counts[index]
            cells.append(Text(str(count) if count else ''))
        chooser.add_row(*cells, key=str(index))
    added = sum(s.lines_added for s in diffstat)
    removed = sum(s.lines_removed for s in diffstat)
    chooser.border_subtitle = f'{len(diffstat)} files · +{added} −{removed}'
