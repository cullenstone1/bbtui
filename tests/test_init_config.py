import io
import stat

import pytest
import yaml

from bbtui.api import AuthenticationError
from bbtui.cli import main
from bbtui.config import Settings
from bbtui.init_config import Account, example_config, render_config, run_init
from bbtui.models import Workspace


def scripted(*answers: str):
    """An `input` stand-in that gives these answers in order, and records the prompts."""
    queue = list(answers)
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return queue.pop(0)

    ask.prompts = prompts  # type: ignore[attr-defined]
    return ask


def test_example_config_is_valid_and_matches_the_settings():
    data = yaml.safe_load(example_config())
    assert set(data) <= set(Settings.model_fields)
    assert data['starred_repos'] == []


def test_render_config_fills_values_and_keeps_comments():
    text = render_config('me@example.com', 'ATATT"x#y', 'acme', ['widgets', 'other/gadgets'])
    data = yaml.safe_load(text)
    assert data['username'] == 'me@example.com'
    assert data['api_token'] == 'ATATT"x#y'  # Quoted, so YAML specials survive.
    assert data['workspace'] == 'acme'
    assert data['starred_repos'] == ['widgets', 'other/gadgets']
    assert '# A *scoped* Atlassian API token' in text
    assert yaml.safe_load(render_config('a', 'b', 'c', []))['starred_repos'] == []


def test_init_writes_a_private_config(tmp_path, monkeypatch):
    path = tmp_path / 'bbtui' / 'config.yaml'
    tokens = scripted('bad-token', 'good-token')
    checked = []

    def check(username, token):
        checked.append((username, token))
        if token == 'bad-token':
            raise AuthenticationError('API Token provided has no Bitbucket scopes.', 401)
        return Account('Me', [Workspace('acme', 'Acme'), Workspace('other', 'Other')])

    ask = scripted('me@example.com', '', '9', 'other', 'widgets, other/gadgets')
    out = io.StringIO()
    assert run_init(path, ask, tokens, out, check) == 0

    assert checked == [('me@example.com', 'bad-token'), ('me@example.com', 'good-token')]
    assert 'no Bitbucket scopes' in out.getvalue()
    assert 'Pick one of the numbers' in out.getvalue()  # '9' isn't a choice.
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    monkeypatch.setenv('BBTUI_CONFIG_FILE', str(path))
    settings = Settings()
    assert settings.username == 'me@example.com'
    assert settings.api_token.get_secret_value() == 'good-token'
    assert settings.workspace == 'other'
    assert settings.starred_repos == ['widgets', 'other/gadgets']


def test_init_keeps_an_existing_config_unless_told(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('username: keep\n')
    out = io.StringIO()
    assert run_init(path, scripted(''), scripted(), out) == 1
    assert path.read_text() == 'username: keep\n'


def test_init_gives_up_when_asked(tmp_path):
    path = tmp_path / 'config.yaml'

    def check(username, token):
        raise AuthenticationError('nope', 401)

    out = io.StringIO()
    assert run_init(path, scripted('me@example.com', 'n'), scripted('t'), out, check) == 1
    assert not path.exists()


def test_cli_points_at_init_when_there_is_no_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('BBTUI_CONFIG_FILE', str(tmp_path / 'missing.yaml'))
    with pytest.raises(SystemExit) as exit_:
        main([])
    assert exit_.value.code == 2
    assert 'Run `bbtui --init`' in capsys.readouterr().err


def test_cli_init_needs_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr('sys.stdin', io.StringIO(''))
    with pytest.raises(SystemExit) as exit_:
        main(['--init'])
    assert exit_.value.code == 2
    assert 'needs a terminal' in capsys.readouterr().err
