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
  comments, and a diff tab with a file chooser on top and one file's diff below. Inline comment
  threads appear under the lines they refer to; comments on lines no longer in the diff are shown
  at the top of the file. Descriptions and comments are rendered as Markdown, with @-mentions
  shown as names.

- **Reviewing**: approve or request changes (each key toggles), and comment with a Markdown
  editor: on the pull request, inline on a diff line, or as a reply to a comment. A cancelled or
  failed comment is kept as a draft for the same spot.

- **Creating pull requests**: `n` on a repository's pull requests opens a form. The source
  defaults to your checked-out branch when bbtui runs inside a clone of that repository, and
  the destination to the repository's development branch. Branches are filtered as you type.
  The title and description are filled from the commits (one commit: its summary; several: the
  branch name, with the commits listed) without overwriting your edits. Default reviewers are
  pre-selected, and the form previews the commits and changed files and warns about an
  already-open pull request from the same branch.

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
| | `c` | Comment: on the PR (Overview), on the cursor line (diff), or reply to the focused comment |
| | `Tab` / click | Focus a comment, to reply to it |
| | `p` | Open the build (failing, else running, else latest) |
| | `o` | Open in the browser |
| | `r` | Refresh |
| | `Esc` | Back |
| Diff | `↑`/`↓`, `j`/`k`, PgUp/PgDn, `g`/`G` | Move the line cursor |
| Comment editor | `Ctrl+S` / `Esc` | Post / cancel (keeps the draft) |

## Layout

```
src/bbtui/
  cli.py            entry point (`bbtui`, `bbtui --check`)
  config.py         settings from YAML + BBTUI_* environment variables
  models.py         typed views of Bitbucket REST v2 resources
  merge.py          merge readiness checks
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

## Development

```sh
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/textual run --dev bbtui.app:BBTUI   # with `textual console` in another terminal
```

## Roadmap

1. ~~Read-only browsing~~
2. Reviewing: ~~approve, request changes, comment (top-level, inline, replies)~~, edit and delete
   your comments, tasks, merge, decline
3. ~~Creating pull requests~~ (adding reviewers beyond the defaults, and PRs from forks, to come)
4. Pipelines: runs per repository, step status, step logs (tailing while running), rerun

Merge checks report what the API shows to non-admins. The repository's own merge rules (for
example "2 approvals required") are branch restrictions, which need repository admin access to
read, so "No blockers found" doesn't guarantee Bitbucket will allow the merge.

Known gaps: the diff isn't syntax-highlighted by language (only +/− colouring), and @-mentions
of people who aren't on the pull request stay as raw `@{account_id}`.
