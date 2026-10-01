from bbtui.config import Settings, config_file_path


def test_loads_yaml_config(tmp_path, monkeypatch):
    config = tmp_path / 'config.yaml'
    config.write_text(
        'username: me@example.com\n'
        'api_token: secret\n'
        'workspace: acme\n'
        'starred_repos: [widgets, other/gadgets]\n'
    )
    monkeypatch.setenv('BBTUI_CONFIG_FILE', str(config))
    settings = Settings()
    assert settings.username == 'me@example.com'
    assert settings.api_token is not None and settings.api_token.get_secret_value() == 'secret'
    assert settings.starred_repos == ['widgets', 'other/gadgets']
    assert settings.missing_required() == []


def test_environment_overrides_yaml(tmp_path, monkeypatch):
    config = tmp_path / 'config.yaml'
    config.write_text('workspace: acme\n')
    monkeypatch.setenv('BBTUI_CONFIG_FILE', str(config))
    monkeypatch.setenv('BBTUI_WORKSPACE', 'other')
    assert Settings().workspace == 'other'


def test_missing_config_file_reports_required_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('BBTUI_CONFIG_FILE', str(tmp_path / 'absent.yaml'))
    assert Settings().missing_required() == ['username', 'api_token', 'workspace']


def test_default_config_path_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv('BBTUI_CONFIG_FILE', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    assert config_file_path() == tmp_path / 'bbtui' / 'config.yaml'
