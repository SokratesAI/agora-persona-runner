"""The owner's first name appears in the front end once, as a stored value.

His issues.md #98: he does not want his own first name standing as a static
string in a repo anyone can read. The comments half of that is already
covered -- `tools.name_scan` reads prose and refuses it there. This is the
other half, and `name_scan` deliberately cannot do it: it only looks at
comments and docstrings, so every one of the eight string literals this test
now forbids was invisible to it.

The design being pinned is the split in `app.js`: `OWNER_RECORD` is the value
CouchDB and his markdown are already keyed on, so it has to keep saying his
name, and `OWNER_LABEL` is what a reader sees. Any other literal is a
rendering site that hardcoded him again, which is what the row was about.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "agora_runner" / "nova_public" / "app.js"

# Case-sensitive for `name_scan`'s reason: a lowercase `edvard` is an
# identifier or a database name being quoted, not his name written out.
NAME = re.compile(r"Edvard")

# The literal that is data. Written out rather than derived from the file, so
# that deleting the declaration fails this test instead of emptying it.
DECLARATION = 'var OWNER_RECORD = "Edvard";'


def _lines():
    return APP.read_text(encoding="utf-8").splitlines()


def test_the_record_constant_is_declared():
    assert DECLARATION in [ln.strip() for ln in _lines()]


def test_the_only_occurrence_outside_a_comment_is_that_declaration():
    offenders = []
    in_block = False
    for number, line in enumerate(_lines(), 1):
        stripped = line.strip()
        # Good enough for this file, which has no block comment opened inside
        # a string: every `/*` here starts at the beginning of a line.
        if in_block:
            if "*/" in line:
                in_block = False
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in line
            continue
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        if not NAME.search(line):
            continue
        if stripped == DECLARATION:
            continue
        offenders.append(f"{number}: {stripped}")
    assert not offenders, (
        "a rendering site hardcodes the owner's name again -- use OWNER_LABEL "
        "for what a reader sees and OWNER_RECORD for a stored value:\n"
        + "\n".join(offenders)
    )


def test_the_displayed_label_is_not_his_name():
    label = [ln for ln in _lines() if ln.strip().startswith("var OWNER_LABEL")]
    assert len(label) == 1, label
    assert not NAME.search(label[0]), label[0]
