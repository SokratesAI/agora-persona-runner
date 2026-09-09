"""The roll's summary line counts bytes, the unit a cycle checks it in.

`prompt.md` carries a standing rule that a shrinking archive is always
the bug and never the roll, and the way a cycle checks it is `wc -c`
against the number `roll_digest` prints. `wc -c` counts bytes; the
summary counted characters. On 2026-09-09 the digest archive held 8,405
multibyte characters, so the tool read a 1,456,801-byte file and called
it 1,448,396 -- and three cycles read that as text being lost, refused
to write, and passed the refusal on. The archive was never touched.

So the property here is not "the number is plausible". It is that the
printed number equals the size of the file on disk, which is the only
thing that makes the shrink rule checkable at all.
"""

import re

from tools.roll_digest import ARCHIVE_TITLE, SPEC
from agora_runner.rolling import run

# Every line carries an em-dash and one carries an emoji, so characters
# and bytes disagree on both sides of the move. An all-ASCII fixture
# passes whichever unit the tool uses and would prove nothing.
LIVE = """---
type: log
---

# Journal — Digest

## Needs Edvard

**Nothing.**

## Next cycle

Check the deploy — 🔴 High.

## Digest

**Cycle 5** (2026-08-11 17:00) — Fifth — 🟠 High.

**Cycle 4** (2026-08-11 16:00) — Fourth — em-dash.

**Cycle 3** (2026-08-11 15:00) — Third — em-dash.

**Cycle 2** (2026-08-11 14:00) — Second — em-dash.

**Cycle 1** (2026-08-11 13:00) — First — em-dash.
"""

ARCHIVE = f"""---
type: log
---

{ARCHIVE_TITLE}

**Cycle 0** (2026-08-11 12:00) — Zeroth — em-dash.
"""


def _roll(tmp_path, capsys, keep):
    live = tmp_path / "live.md"
    archive = tmp_path / "archive.md"
    live.write_text(LIVE, encoding="utf-8")
    archive.write_text(ARCHIVE, encoding="utf-8")
    run(SPEC, ["--live", str(live), "--archive", str(archive), "--keep", str(keep)])
    return capsys.readouterr().out, live, archive


def test_the_printed_sizes_are_the_bytes_on_disk(tmp_path, capsys):
    out, live, archive = _roll(tmp_path, capsys, keep=2)
    numbers = re.search(
        r"(\d+) -> (\d+) bytes live, (\d+) -> (\d+) bytes archived", out
    )
    assert numbers, out
    before_live, after_live, before_archive, after_archive = (
        int(n) for n in numbers.groups()
    )
    # The "before" pair is the input as it sat on disk, and the "after"
    # pair is what the tool then wrote there -- so both are checkable
    # against the filesystem rather than against another count of mine.
    assert before_live == len(LIVE.encode("utf-8"))
    assert before_archive == len(ARCHIVE.encode("utf-8"))
    assert after_live == live.stat().st_size
    assert after_archive == archive.stat().st_size


def test_the_multibyte_fixture_would_expose_a_character_count(tmp_path, capsys):
    # The precondition the test above rests on: if the summary counted
    # characters instead, these numbers would differ. Without this a
    # future all-ASCII rewrite of the fixture would leave the assertions
    # above passing while measuring nothing.
    assert len(LIVE.encode("utf-8")) != len(LIVE)
    assert len(ARCHIVE.encode("utf-8")) != len(ARCHIVE)
    out, _, archive = _roll(tmp_path, capsys, keep=2)
    assert str(archive.stat().st_size) in out
    assert str(len(archive.read_text(encoding="utf-8"))) not in out
