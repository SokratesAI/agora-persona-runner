"""The owner's name stays out of this public repo's prose, and stays in its data."""
import subprocess
from pathlib import Path

import pytest

from tools import name_scan

ROOT = Path(__file__).resolve().parent.parent

# Test fixtures are copies of his real vault files -- headings, author labels,
# the text of his own captures. Renaming inside one would make the fixture stop
# resembling the thing it stands in for, which is the only reason it exists.
EXEMPT_PREFIXES = ("tests/fixtures/",)


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split("\n")
    return [p for p in out if p and not p.startswith(EXEMPT_PREFIXES)]


def test_no_owner_name_in_any_comment_or_docstring():
    report = name_scan.scan([str(ROOT / p) for p in tracked_files()])
    assert report == [], (
        "the owner asked for his name out of public repos "
        "(issues.md 2026-08-24). Write 'the owner':\n" + "\n".join(report))


def test_a_python_comment_is_prose():
    assert name_scan.hits("x.py", "# Edvard asked for this\nx = 1\n")


def test_a_python_docstring_is_prose():
    assert name_scan.hits("x.py", '"""What Edvard wanted."""\n')


def test_a_python_string_literal_is_data_and_is_left_alone():
    # `sender == "Edvard"` is an Agora message field and `"## Needs Edvard"` is  (not-prose: quoting a literal)
    # a heading the written archive really contains. Renaming either one
    # anonymises nothing and breaks the page -- which is what the first,
    # blind version of this sweep did before the suite stopped it.
    assert name_scan.hits("x.py", 'MARKER = "\\n## Needs Edvard\\n"\n') == []
    assert name_scan.hits("x.py", 'if sender == "Edvard":\n    pass\n') == []


def test_a_docstring_that_is_not_first_is_an_ordinary_string():
    src = 'def f():\n    x = 1\n    "Edvard"\n'
    assert name_scan.hits("x.py", src) == []


def test_js_and_css_comments_are_prose_and_their_strings_are_not():
    assert name_scan.hits("a.js", "// Edvard reported this\n")
    assert name_scan.hits("a.js", "/* Edvard reported this */\n")
    assert name_scan.hits("a.js", 'const who = "Edvard";\n') == []
    assert name_scan.hits("a.css", "/* Edvard, 2026-08-14 */\n.x { color: red }\n")


def test_markdown_is_prose_end_to_end():
    assert name_scan.hits("README.md", "Written for Edvard.\n")


def test_a_not_prose_line_is_exempt_and_only_that_line():
    # The case it exists for: a comment quoting the literal on the line below it.
    src = ('# sender="Edvard" is not decoration  # not-prose\n'
           '# and Edvard asked for this\n')
    found = name_scan.hits("x.py", src)
    assert [line for line, _ in found] == [2]


def test_an_unknown_suffix_reports_nothing():
    assert name_scan.prose("a.bin", "Edvard") == []


def test_a_lowercase_identifier_in_a_comment_is_left_alone():
    # `roll_needs_edvard.py` is a module, `["edvard"]` is a board-path key, and
    # `edvard` is the actual name of a CouchDB database configured outside this
    # repo. A comment naming one of those is quoting code.
    assert name_scan.hits("x.py", "# see roll_needs_edvard.py for the rest\n") == []
    assert name_scan.hits("x.py", '# BOARD_PATHS["issues"]["edvard"] moved\n') == []


def test_main_exits_nonzero_on_a_hit(tmp_path):
    bad = tmp_path / "x.py"
    bad.write_text("# Edvard\n")
    assert name_scan.main([str(bad)]) == 1
    good = tmp_path / "y.py"
    good.write_text("# the owner\n")
    assert name_scan.main([str(good)]) == 0


def test_a_syntactically_broken_python_file_does_not_crash_the_scan():
    assert name_scan.hits("x.py", "def (:\n") == []
