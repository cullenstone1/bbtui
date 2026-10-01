from bbtui import terminal


def fake_client(monkeypatch, client):
    monkeypatch.setattr(terminal, 'tmux_client', lambda: client)


def test_tmux_without_rgb_uses_256_colours(monkeypatch):
    fake_client(monkeypatch, ('xterm-256color', {'bpaste', 'focus', 'title'}))
    system, reason = terminal.colour_override({})
    assert system == '256'
    assert '24-bit' in reason


def test_tmux_with_rgb_is_left_alone(monkeypatch):
    fake_client(monkeypatch, ('xterm-256color', {'RGB', 'focus'}))
    assert terminal.colour_override({}) is None


def test_sixteen_colour_tmux_client(monkeypatch):
    fake_client(monkeypatch, ('xterm', {'focus'}))
    assert terminal.colour_override({})[0] == 'standard'


def test_outside_tmux_or_explicit_choice_is_left_alone(monkeypatch):
    fake_client(monkeypatch, None)
    assert terminal.colour_override({}) is None
    fake_client(monkeypatch, ('xterm-256color', set()))
    assert terminal.colour_override({'TEXTUAL_COLOR_SYSTEM': 'truecolor'}) is None


def test_tmux_client_needs_tmux(monkeypatch):
    monkeypatch.delenv('TMUX', raising=False)
    assert terminal.tmux_client() is None


def test_detached_session_is_left_alone(monkeypatch):
    fake_client(monkeypatch, ('', set()))
    assert terminal.colour_override({}) is None
