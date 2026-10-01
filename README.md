# bbtui

A terminal UI for Bitbucket Cloud, built with [Textual](https://textual.textualize.io/) and
modelled on [jiratui](https://github.com/whyisdifficult/jiratui). The goal is to do pull request
review, pull request creation and pipeline monitoring without leaving the terminal.

## Status

Milestone 1 (read-only browsing) is in place:

- **Dashboard**: starred repositories from the config, recently updated repositories, and
  server-side search by repository name.
- **Pull requests**: open, merged or declined pull requests for a repository, with review status.
- **Pull request detail**: an overview with reviewers, the rendered description and general
  comments, and a diff tab with a file chooser on top and one file's diff below. Inline comment
  threads appear under the lines they refer to; comments on lines no longer in the diff are shown
  at the top of the file. Descriptions and comments are rendered as Markdown, with @-mentions
  shown as names.

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
| | `Enter` | Open the repository's pull requests |
| | `r` | Refresh |
| Pull requests | `Enter` | Open the pull request |
| | `s` | Cycle open / merged / declined |
| | `o` | Open in the browser |
| | `Esc` | Back |
| Pull request | `1` / `2` | Overview / Diff |
| | `[` / `]` | Previous / next file |
| | `Enter` (file list) | Move into the diff; `Esc` goes back to the file list |
| | `o` | Open in the browser |
| | `r` | Refresh |
| | `Esc` | Back |

## Layout

```
src/bbtui/
  cli.py            entry point (`bbtui`, `bbtui --check`)
  config.py         settings from YAML + BBTUI_* environment variables
  models.py         typed views of Bitbucket REST v2 resources
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
2. Reviewing: approve, request changes, comment (top-level, inline, replies), merge, decline
3. Creating pull requests: branch pickers, title/description, reviewers
4. Pipelines: runs per repository, step status, step logs (tailing while running), rerun

Known gaps: the diff isn't syntax-highlighted by language (only +/− colouring), and @-mentions
of people who aren't on the pull request stay as raw `@{account_id}`.
