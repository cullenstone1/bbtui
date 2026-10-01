from bbtui.diff import parse_diff

DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,4 +10,5 @@ def main():
 keep
--- a removed line that looks like a header
+++ an added line that looks like a header
+another added
 tail
\\ No newline at end of file
diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+hello
diff --git a/old name.py b/new name.py
similarity index 100%
rename from old name.py
rename to new name.py
diff --git a/gone.bin b/gone.bin
deleted file mode 100644
Binary files a/gone.bin and /dev/null differ
"""


def test_splits_files_and_paths():
    files = parse_diff(DIFF)
    assert [(f.old_path, f.new_path) for f in files] == [
        ('src/app.py', 'src/app.py'),
        (None, 'new.txt'),
        ('old name.py', 'new name.py'),
        ('gone.bin', None),
    ]


def test_line_numbers_and_kinds():
    app = parse_diff(DIFF)[0]
    rows = [(line.kind, line.old, line.new) for line in app.lines]
    assert rows == [
        ('hunk', None, None),
        ('context', 10, 10),
        ('removed', 11, None),
        ('added', None, 11),
        ('added', None, 12),
        ('context', 12, 13),
        ('note', None, None),
    ]


def test_header_lookalikes_inside_hunks_are_content():
    app = parse_diff(DIFF)[0]
    assert app.lines[2].text.startswith('--- a removed')
    assert app.new_path == 'src/app.py'


def test_meta_lines_are_kept_without_index_and_headers():
    files = parse_diff(DIFF)
    assert [line.text for line in files[2].lines] == [
        'similarity index 100%',
        'rename from old name.py',
        'rename to new name.py',
    ]
    assert files[3].lines[-1].text.startswith('Binary files')


def test_comment_anchors():
    app = parse_diff(DIFF)[0]
    assert app.line_index(line_to=12, line_from=None) == 4
    assert app.line_index(line_to=None, line_from=11) == 2
    assert app.line_index(line_to=99, line_from=None) is None
