# bbtui

A terminal UI for Bitbucket Cloud, built with [Textual](https://textual.textualize.io/) and
modelled on [jiratui](https://github.com/whyisdifficult/jiratui). The goal is to do pull request
review, pull request creation and pipeline monitoring without leaving the terminal.

## Status

Browsing and the first part of reviewing are in place:

- **Dashboard**: starred repositories from the config, recently updated repositories,
  server-side search by repository name, and your open pull requests across the workspace with
  their approvals, comments and build status.
- **Pull requests**: open, merged or declined pull requests for a repository, with review status.
- **Pull request detail**: an overview with merge checks (draft, conflicts, approvals and
  changes requested, builds, open tasks), build statuses (`p` opens the pipeline), reviewers, the rendered description and general
  comments, and a diff tab with a file chooser on top and one file's diff below, syntax
  highlighted by file type with added/removed lines tinted. Inline comment
  threads appear under the lines they refer to; comments on lines no longer in the diff are shown
  at the top of the file. Descriptions and comments are rendered as Markdown, with @-mentions
  shown as names.

- **Reviewing**: approve or request changes (each key toggles), and comment with a Markdown
  editor: on the pull request, inline on a diff line, or as a reply to a comment. A cancelled or
  failed comment is kept as a draft for the same spot. Mark drafts ready (or convert back), and
  merge with the destination branch's allowed strategies (its default pre-selected), an editable
  commit message and the close-source-branch option; blockers from the merge checks are shown
  first.

- **Creating pull requests**: `n` on a repository's pull requests opens a form. The source
  defaults to your checked-out branch when bbtui runs inside a clone of that repository, and
  the destination to the repository's development branch. Branches are filtered as you type.
  The title and description are filled from the commits (one commit: its summary; several: the
  branch name, with the commits listed) without overwriting your edits. Default reviewers are
  pre-selected, and the form previews the commits and changed files and warns about an
  already-open pull request from the same branch.

- **Pipelines**: a run screen with the run's summary, its steps, a list of likely failure lines
  (`[FAIL]`, `error:`, `FAILED`, `make: ***`, tracebacks, ...) and a fast log viewer that keeps
  colours. A failed run opens on its last likely failure. Running pipelines are polled and their
  logs tailed. Re-run and stop ask for confirmation. Open a run from a pull request's builds
  (`p`), from a repository's pipeline list (`P` on its pull requests), or from the dashboard's
  scheduled pipelines panel, which shows the latest run of each schedule (e.g. a nightly) in
  your starred repositories. You get a notification when a build on one of your pull requests
  finishes.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e . --group dev --group lint --group test
cp bbtui.example.yaml ~/.config/bbtui/config.yaml   # then fill it in
.venv/bin/bbtui --check                             # verify credentials, list workspaces
.venv/bin/bbtui
```

`uv sync --all-groups` works too.

## Keys

| Screen | Key | Action |
| --- | --- | --- |
| Everywhere | `q` | Quit |
| Dashboard | `/` | Search repositories (Enter to run, Esc to clear) |
| | `Enter` | Open the repository's pull requests, or your pull request |
| | `Tab` | Move between panels |
| | `r` | Refresh |
| Pull requests | `Enter` | Open the pull request |
| | `s` | Cycle open / merged / declined |
| | `n` | New pull request |
| | `P` | The repository's pipelines |
| | `o` | Open in the browser |
| | `Esc` | Back |
| New pull request | `Ctrl+S` | Create |
| | `↓` (in a branch filter) | Move into the branch list |
| | `Esc` | Cancel (asks again if you edited the title or description) |
| Pull request | `1` / `2` | Overview / Diff |
| | `[` / `]` | Previous / next file |
| | `Enter` (file list) | Move into the diff; `Esc` goes back to the file list |
| | `a` | Approve, or remove your approval |
| | `x` | Request changes, or withdraw the request |
| | `d` | Mark a draft ready for review, or convert back to a draft (asks first) |
| | `m` | Merge (not drafts) |
| | `c` | Comment: on the PR (Overview), on the cursor line (diff), or reply to the focused comment |
| | `Tab` / click | Focus a comment, to reply to it |
| | `p` | Open the build (failing, else running, else latest) in bbtui |
| | `o` | Open in the browser |
| | `r` | Refresh |
| | `Esc` | Back |
| Pipelines | `m` / `f` | Only mine / only failed (toggles) |
| | `Enter` | Open the run |
| Pipeline run | `e` / `E` | Next / previous likely failure |
| | `/`, then `n` / `N` | Search the log, next / previous match |
| | `R` / `s` | Re-run / stop (asks first) |
| | `Enter` (steps, failures) | Show that step's log / jump to that line |
| Diff | `↑`/`↓`, `j`/`k`, PgUp/PgDn, `g`/`G` | Move the line cursor |
| Comment editor | `Ctrl+S` / `Esc` | Post / cancel (keeps the draft) |
| Merge dialog | `Ctrl+S` / `Esc` | Merge / cancel |

## Layout

```
src/bbtui/
  cli.py            entry point (`bbtui`, `bbtui --check`)
  config.py         settings from YAML + BBTUI_* environment variables
  models.py         typed views of Bitbucket REST v2 resources
  merge.py          merge readiness checks
  logs.py           pipeline log decoding, sanitising, failure detection
  highlight.py      syntax highlighting for diffs (per hunk, old and new sides)
  git.py            reading the local checkout (default source branch)
  pull_request_defaults.py   default title and description for new pull requests
  diff.py           unified diff parsing (per file, with old/new line numbers)
  text.py           sanitising remote text, time formatting
  api/client.py     httpx transport: auth, error mapping, pagination
  api/api.py        endpoints returning models
  app.py            the Textual app
  screens/          dashboard, pull request list, pull request detail
  widgets/          comment cards, the single-file diff view
  bbtui.tcss        styles
```

Remote text is always rendered as `rich.text.Text` rather than markup, and control characters are
stripped, so titles, descriptions and comments can't restyle the UI or move the terminal cursor.

## Colours

bbtui needs a terminal that advertises 256 colours or more; with only 16 (e.g. tmux's default
`TERM=screen`), diff tints and theme colours collapse to greys, and bbtui falls back to plain
green/red diffs and says so at startup. For tmux, add to `~/.tmux.conf`:

```
set -g default-terminal "tmux-256color"
set -as terminal-features ",*:RGB"
```

and make sure the outer terminal exports `COLORTERM=truecolor` (most modern terminals do).

When tmux can't pass 24-bit colour through (no `RGB` feature), bbtui notices and draws with 256
colours itself, which keeps diff tints green and red rather than letting tmux turn them grey.
Set `TEXTUAL_COLOR_SYSTEM` (`truecolor`, `256`, `standard`) to override.

## Development

```sh
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/textual run --dev bbtui.app:BBTUI   # with `textual console` in another terminal
```

## Roadmap

1. ~~Read-only browsing~~
2. Reviewing: ~~approve, request changes, comment (top-level, inline, replies), mark ready,
   merge~~, edit and delete your comments, tasks, decline
3. ~~Creating pull requests~~ (adding reviewers beyond the defaults, and PRs from forks, to come)
4. ~~Pipelines: runs per repository, step status, step logs (tailing while running), rerun~~;
   running custom pipelines with variables, test reports

Merge checks report what the API shows to non-admins. The repository's own merge rules (for
example "2 approvals required") are branch restrictions, which need repository admin access to
read, so "No blockers found" doesn't guarantee Bitbucket will allow the merge.

Known gaps: changed words within a line aren't highlighted yet, and @-mentions of people who
aren't on the pull request stay as raw `@{account_id}`.
