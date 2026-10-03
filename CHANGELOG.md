# Changelog

All notable changes to bbtui. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `c` on the dashboard or a repository's pull requests shows the repository's HTTPS and SSH
  clone links; `h` / `s` copies one.

## [0.1.0]

First release.

### Added

- `bbtui --init`: guided setup that checks your API token and writes the config file.
- Dashboard: pull requests waiting for your review (drafts optional, with
  `review_include_drafts`), starred and recently updated repositories, repository search, your
  open pull requests across the workspace, and the latest runs of your starred repositories'
  scheduled pipelines (e.g. nightlies).
- Pull requests: list by state; detail with merge checks, builds, reviewers, the rendered
  description, history and comments; a per-file diff with syntax highlighting, inline comment
  threads, outdated comments and collapsible resolved threads.
- Reviewing: approve, request changes, comment (general, inline and replies, with drafts kept),
  mark ready or back to draft, and merge with the branch's allowed strategies.
- Creating pull requests, with defaults from your checkout and the commits, default reviewers,
  and a preview of the commits and changed files.
- Commits: browse a branch's or tag's commits (optionally only those not on another branch or
  tag) and read each commit's diff; tagged commits are labelled. `C` on a repository's pull
  requests.
- Pipelines: run list, run screen with steps, likely failures and a fast log viewer (tailing
  running steps), re-run and stop, and a notification when a build on your pull request finishes.
- `u` shows the URL of what you're looking at, to copy or open; `h` `j` `k` `l` navigation.
- Optional idle timeout back to the dashboard.

[Unreleased]: https://github.com/cullenstone1/bbtui/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cullenstone1/bbtui/releases/tag/v0.1.0
