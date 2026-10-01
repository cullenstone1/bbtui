from bbtui.logs import LineDecoder, is_failure, plain, sanitize


def test_sanitize_keeps_colours_and_drops_other_escapes():
    assert sanitize('\x1b[1;31m[FAIL] x\x1b[0m') == '\x1b[1;31m[FAIL] x\x1b[0m'
    assert sanitize('a\x1b[2Jb\x1b[10;5Hc\x1b]0;title\x07d\x07') == 'abcd'
    assert sanitize('downloading 10%\rdownloading 100%') == 'downloading 100%'
    assert plain('\x1b[32mok\x1b[0m') == 'ok'


def test_failure_lines():
    failures = [
        '\x1b[1;31m[FAIL] src/common_lib is not pointing to any allowed branch\x1b[0m',
        '[  FAILED  ] Suite.Test (12 ms)',
        'src/a.cpp:12:3: error: expected ;',
        'make[2]: *** [all] Error 2',
        'Traceback (most recent call last)',
        'Process finished with exit code 2',
        'fatal: not a git repository',
    ]
    not_failures = [
        '100% tests passed, 0 tests failed out of 703',
        'WARN com.rti Enumerator NONE is already defined; failed to comply',
        'src/a.cpp:3: warning: unused variable; error: no',
        'Merged test suites, total number tests is 0, with 0 failures and 0 errors',
        'Process finished with exit code 0',
        '-Werror is on',
    ]
    assert [line for line in failures if not is_failure(line)] == []
    assert [line for line in not_failures if is_failure(line)] == []


def test_decoder_handles_split_lines_and_characters():
    decoder = LineDecoder()
    data = 'one\ntwo ✔ three\nfour'.encode()
    split = data.index('✔'.encode()) + 1  # Split inside the multi-byte character.
    assert decoder.feed(data[:split]) == ['one']
    assert decoder.feed(data[split:]) == ['two ✔ three']
    assert decoder.finish() == ['four']
    assert decoder.finish() == []
