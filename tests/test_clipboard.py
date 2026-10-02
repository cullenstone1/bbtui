from bbtui import clipboard


def test_commands_follow_the_session(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, 'which', lambda name: f'/usr/bin/{name}')
    assert clipboard.clipboard_commands({'WAYLAND_DISPLAY': 'w', 'DISPLAY': ':0'})[:2] == [
        ['wl-copy'],
        ['xclip', '-selection', 'clipboard'],
    ]
    assert clipboard.clipboard_commands({}) == [['pbcopy']]


def test_only_installed_tools(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, 'which', lambda name: name == 'xclip' or None)
    assert clipboard.clipboard_commands({'WAYLAND_DISPLAY': 'w', 'DISPLAY': ':0'}) == [
        ['xclip', '-selection', 'clipboard']
    ]


def test_copy_falls_through_failing_tools(monkeypatch):
    monkeypatch.setattr(clipboard, 'clipboard_commands', lambda: [['false'], ['cat']])
    assert clipboard.copy_with_tool('x') == 'cat'
    monkeypatch.setattr(clipboard, 'clipboard_commands', lambda: [['false']])
    assert clipboard.copy_with_tool('x') is None
