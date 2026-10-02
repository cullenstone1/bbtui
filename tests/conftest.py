import os

import pytest


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep your own config file and BBTUI_* variables out of the tests."""
    for name in list(os.environ):
        if name.startswith('BBTUI_'):
            monkeypatch.delenv(name)
    monkeypatch.setenv('BBTUI_CONFIG_FILE', str(tmp_path / 'no-config.yaml'))
