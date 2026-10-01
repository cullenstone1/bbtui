from dataclasses import dataclass


@dataclass(frozen=True)
class WatchedBuild:
    """A running build on one of your pull requests, to report when it finishes."""

    workspace: str
    repo_slug: str
    pr_id: int
    key: str
    label: str
