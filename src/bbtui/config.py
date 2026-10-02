"""Settings, loaded from a YAML file with `BBTUI_*` environment overrides."""

import os
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

APP_NAME = 'bbtui'
DEFAULT_BASE_URL = 'https://api.bitbucket.org/2.0'


def config_file_path() -> Path:
    """The YAML config file: `$BBTUI_CONFIG_FILE`, else `$XDG_CONFIG_HOME/bbtui/config.yaml`."""
    if explicit := os.environ.get('BBTUI_CONFIG_FILE'):
        return Path(explicit).expanduser()
    config_home = os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config'
    return Path(config_home) / APP_NAME / 'config.yaml'


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='BBTUI_', extra='ignore')

    username: str | None = None
    """The Atlassian account email that owns the API token."""
    api_token: SecretStr | None = None
    """A scoped Atlassian API token with Bitbucket scopes (unscoped Jira tokens are rejected)."""
    workspace: str | None = None
    """The workspace the TUI opens in."""
    starred_repos: list[str] = []
    """Repositories pinned to the dashboard, as `repo_slug` or `workspace/repo_slug`."""
    recent_repos_limit: int = 10
    """How many recently updated repositories to show on the dashboard."""
    close_source_branch: bool = False
    """Whether new pull requests close their source branch on merge, by default."""
    syntax_theme: str | None = None
    """A Pygments style for diffs (e.g. monokai, dracula, github-dark); defaults to one that
    suits the app theme."""
    theme: str = 'textual-dark'
    """The name of a Textual theme."""
    idle_timeout_minutes: float = 0
    """Go back to the dashboard after this many minutes without a key press, click or scroll
    (0 turns it off). Never while you're writing a comment or a pull request."""
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=config_file_path()),
        )

    def missing_required(self) -> list[str]:
        """Names of the settings that must be set before the TUI can start."""
        missing = []
        if not self.username:
            missing.append('username')
        if not self.api_token or not self.api_token.get_secret_value():
            missing.append('api_token')
        if not self.workspace:
            missing.append('workspace')
        return missing
