"""The five single-cell board writers read each document once, not twice.

Issue #203, the owner's decision that the boards get a real schema. Each of
`board_status`, `board_priority`, `board_size`, `board_milestone` and
`board_project` used to parse the file it is editing **twice** inside one
read-modify-write: once inside `check`, which took the two markdown strings
and parsed them itself, and once more in `main` to print what the cell
moved from. On a string that is free and the two reads can never disagree.
Once the source is a CouchDB range query it is two round trips a concurrent
write can land between -- so the guard would pass against one version of the
board and the line printed to the operator would describe another.

`check` is now `check_from_contents`, which takes the parsed record sets and
the bullet stream and reaches for no document at all, and `main` does every
read. That is the same split the five readers converted before these did
(`nova_next`, `top_board_rows`, `nova_idea_pool`, `nova_site`), and no
markdown door is written here because nothing outside `main` and these tests
ever called `check`.

The count is the assertion, so it is taken in each tool's **own** module
namespace: `from agora_runner.nova_boards import parse_board` binds a second
name, and a counter installed on `nova_boards` alone would miss every call
these modules make.
"""

import importlib

import pytest

# The column order is `parse_board`'s, which reads by POSITION and ignores
# the header text: # | Item | Status | Updated | Priority | Project | Size |
# Milestone. This fixture spelled a different order until Cycle 1317 and
# every row in it parsed shifted -- #100's status read as `Nova` and its
# project as `08-24`. Nothing failed, because every test in here asserts on
# a change rather than on a value, which is exactly how a misaligned
# fixture survives: it is wrong in the same way on both sides of the
# comparison.
BOARD = """---
type: log
---

# Nova — Ideas

## Entries

- 2026-08-26 (Cycle 480) — a bullet nothing here may touch

## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone |
|---|------|--------|---------|----------|---------|------|-----------|
| [[#100 — Weekly work\\|100]] | Weekly work | 🟡 In progress | 08-24 | 🟠 High | Nova | 🅼 Medium | Cost |
| [[#104 — Metered API\\|104]] | Metered API | ⚪ Backlog | 08-24 | 🟠 High | Nova | 🅼 Medium | Cost |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

# Details

### #100 — Weekly work

Three heartbeats, one prompt file each.

### #104 — Metered API

Body text nothing here may touch.
"""

# One successful, cell-moving invocation per tool. Every one is a `--dry-run`
# so the count is of the read path only and no test writes a board.
WRITERS = [
    ("tools.board_status", ["--number", "100", "--status", "in-progress"]),
    ("tools.board_priority", ["--number", "100", "--priority", "medium"]),
    ("tools.board_size", ["--number", "100", "--size", "large"]),
    ("tools.board_milestone", ["--number", "100", "--milestone", "Quota"]),
    ("tools.board_project", ["--number", "100", "--project", "Agora"]),
]


@pytest.mark.parametrize("module_name,args", WRITERS)
def test_each_writer_reads_the_document_once_per_version(tmp_path, monkeypatch, module_name, args):
    module = importlib.import_module(module_name)
    path = tmp_path / "ideas.md"
    path.write_text(BOARD, encoding="utf-8")

    counts = {"parse_board": 0, "parse_notes": 0}
    for name in counts:
        real = getattr(module, name)

        def counting(markdown, _real=real, _name=name):
            counts[_name] += 1
            return _real(markdown)

        monkeypatch.setattr(module, name, counting)

    assert module.main(["--file", str(path), "--dry-run", *args]) == 0
    # Two versions of one document -- `before` and `after` -- and each is
    # read exactly once. Three would mean a read went back to the source.
    assert counts == {"parse_board": 2, "parse_notes": 2}


@pytest.mark.parametrize("module_name,args", WRITERS)
def test_the_guard_cannot_reach_a_document(tmp_path, monkeypatch, module_name, args):
    """`check_from_contents` takes what it compares; it fetches nothing.

    The count above is satisfied by a guard that parses once and a `main`
    that parses once, which is not the property that survives the switchover
    -- the guard has to hold no reference to the source at all. So this runs
    the same write with the module's parsers replaced by something that
    raises, after `main` has taken its own reads.
    """
    module = importlib.import_module(module_name)
    path = tmp_path / "ideas.md"
    path.write_text(BOARD, encoding="utf-8")

    guard = getattr(module, "check_from_contents")

    def no_document_here(*a, **k):
        for name in ("parse_board", "parse_notes"):
            monkeypatch.setattr(module, name, _refuse)
        return guard(*a, **k)

    monkeypatch.setattr(module, "check_from_contents", no_document_here)
    assert module.main(["--file", str(path), "--dry-run", *args]) == 0


def _refuse(*a, **k):
    raise AssertionError("the guard reached back to the document it was handed")


# The name each tool calls to produce the `after` document. Patching it is how
# a test makes the write go wrong without touching `nova_boards`.
MUTATORS = {
    "tools.board_status": "set_row_status",
    "tools.board_priority": "set_row_priority",
    "tools.board_size": "set_row_size",
    "tools.board_milestone": "set_row_milestone",
    "tools.board_project": "set_row_project",
}


def _damage(module_name, args, tmp_path, monkeypatch, damage):
    """Run one writer with its mutator replaced by `damage`, and return the code."""
    module = importlib.import_module(module_name)
    path = tmp_path / "ideas.md"
    path.write_text(BOARD, encoding="utf-8")
    real = getattr(module, MUTATORS[module_name])

    # The damage goes on top of the write the caller actually asked for, or
    # the guard refuses for the plainest reason there is -- "the cell did not
    # move" -- and the test passes without ever reaching what it is about.
    def damaged(before, *a, **k):
        written = real(before, *a, **k)
        return None if written is None else damage(written)

    monkeypatch.setattr(module, MUTATORS[module_name], damaged)
    return module.main(["--file", str(path), "--dry-run", *args])


@pytest.mark.parametrize("module_name,args", WRITERS)
def test_main_refuses_a_write_that_ate_a_bullet(tmp_path, monkeypatch, module_name, args):
    """The bullet-stream half of the guard, driven through `main`.

    Every existing test for these guards calls the guard as a function, so
    nothing proved `main` hands it the *right* two documents. It does not
    change the bullets itself, so a `main` that read the after-stream off the
    before-document -- or a guard whose comparison was deleted -- passes every
    other test in this repo and lets a write that deleted one of his captures
    through.
    """
    assert _damage(module_name, args, tmp_path, monkeypatch, _eat_a_bullet) == 1


@pytest.mark.parametrize("module_name,args", WRITERS)
def test_main_refuses_a_write_that_grew_a_row_from_nowhere(
    tmp_path, monkeypatch, module_name, args
):
    """The row-count half, which nothing exercised in the adding direction."""
    assert _damage(module_name, args, tmp_path, monkeypatch, _add_a_row) == 1


@pytest.mark.parametrize("module_name,args", WRITERS)
def test_main_refuses_a_write_that_moved_a_row_it_was_not_asked_about(
    tmp_path, monkeypatch, module_name, args
):
    """The other-rows half, driven through `main` for the same reason.

    A `main` that handed the guard the after-document twice compares a board
    to itself, which can never report anything, and every direct test of the
    guard stays green because those build both sides themselves.
    """
    assert _damage(module_name, args, tmp_path, monkeypatch, _move_another_row) == 1


# The two damages, each applied to the document the tool just wrote rather
# than to `BOARD`. Building them off the constant instead is the mistake this
# comment exists for: it throws the asked-for edit away, so every tool refuses
# with "the cell did not move" and the test passes without reaching its
# subject. Each asserts it actually changed something, so a fixture edit that
# breaks the substring fails the test instead of quietly making it vacuous.
BULLET = "- 2026-08-26 (Cycle 480) — a bullet nothing here may touch\n"
# It moves the RATING and not the status, and that is the whole strength of
# it. `board_row.check_from_contents` compared only `title` and `status` on a
# pre-existing row until Cycle 1317, so a status damage is caught by the
# narrow guard and the wide one alike and proves nothing about which of the
# two is in the file. Measured: with the widening reverted, a status damage
# leaves every test in here green and a rating damage fails this one.
OPEN_ROW = "| Metered API | ⚪ Backlog | 08-24 | 🟠 High |"
MOVED_ROW = "| Metered API | ⚪ Backlog | 08-24 | 🔴 Immediately |"


def _eat_a_bullet(written):
    damaged = written.replace(BULLET, "")
    assert damaged != written, "the fixture's bullet is not in the written board"
    return damaged


def _move_another_row(written):
    damaged = written.replace(OPEN_ROW, MOVED_ROW)
    assert damaged != written, "the fixture's second row is not in the written board"
    return damaged


# A row that appeared out of nowhere, which is the damage NONE of these
# guards had a test for. Every one of them owns a row-count check, and
# deleting `board_status`' left the whole board suite green (mutation run,
# Cycle 1317): a row that fell OFF is reported by the per-row loop as well,
# so the count check is load-bearing for exactly one direction and that
# direction was untested. The five single-cell writers expect no change and
# `board_row` expects +1, so one extra row fails both.
ADDED_ROW = (
    "| [[#999 — Smuggled in\\|999]] | Smuggled in | ⚪ Backlog | 08-24 "
    "| 🟠 High | Nova | 🅼 Medium | Cost |\n"
)


def _add_a_row(written):
    # Inserted as a whole LINE after #104's whole line. Splicing after
    # `OPEN_ROW` instead cuts that row's remaining cells onto the new line,
    # so the guard reports #104 as damaged and the count check it is aiming
    # at never has to fire -- a green test that measured the wrong thing.
    lines = written.splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if OPEN_ROW in line)
    damaged = "".join(lines[: at + 1] + [ADDED_ROW] + lines[at + 1 :])
    assert damaged != written, "the fixture's second row is not in the written board"
    return damaged


# --- The two multi-cell writers -------------------------------------------
#
# `board_row` adds a row and `board_untag_project` rewrites a title and a
# project cell together, so neither fits the parametrised shape above: one
# mutator returns a `(markdown, number)` pair and the other returns a
# triple, and the row count is *expected* to move for one of them. The
# property under test is identical -- each version of the document is
# parsed once, by `main`, and the guard reaches for nothing -- so the tests
# live here rather than beside their tools.

UNTAG_BOARD = BOARD.replace(
    "| Metered API |", "| (Project: NAS) Metered API |"
).replace("#104 — Metered API\\|104", "#104 — (Project: NAS) Metered API\\|104")


def _count_parses(module, monkeypatch):
    """Count `parse_board`/`parse_notes` in this module's OWN namespace."""
    counts = {"parse_board": 0, "parse_notes": 0}
    for name in counts:
        real = getattr(module, name)

        def counting(markdown, _real=real, _name=name):
            counts[_name] += 1
            return _real(markdown)

        monkeypatch.setattr(module, name, counting)
    return counts


def test_board_row_reads_the_document_once_per_version(tmp_path, monkeypatch):
    import tools.board_row as module

    path = tmp_path / "issues.md"
    path.write_text(BOARD, encoding="utf-8")
    counts = _count_parses(module, monkeypatch)

    assert module.main([
        "--file", str(path), "--title", "A third thing",
        "--priority", "high", "--dated", "09-09", "--dry-run",
    ]) == 0
    assert counts == {"parse_board": 2, "parse_notes": 2}


def test_board_row_guard_cannot_reach_a_document(tmp_path, monkeypatch):
    import tools.board_row as module

    path = tmp_path / "issues.md"
    path.write_text(BOARD, encoding="utf-8")
    guard = module.check_from_contents

    def no_document_here(*a, **k):
        for name in ("parse_board", "parse_notes"):
            monkeypatch.setattr(module, name, _refuse)
        return guard(*a, **k)

    monkeypatch.setattr(module, "check_from_contents", no_document_here)
    assert module.main([
        "--file", str(path), "--title", "A third thing",
        "--priority", "high", "--dated", "09-09", "--dry-run",
    ]) == 0


def _damage_board_row(tmp_path, monkeypatch, damage):
    import tools.board_row as module

    path = tmp_path / "issues.md"
    path.write_text(BOARD, encoding="utf-8")
    real = module.add_row

    # `add_row` returns `(markdown, number)`, so the damage lands on the
    # first half of the pair and the allocated number travels untouched --
    # a test that damaged the number instead would be testing `add_row`.
    def damaged(before, *a, **k):
        written, number = real(before, *a, **k)
        return (None if written is None else damage(written)), number

    monkeypatch.setattr(module, "add_row", damaged)
    return module.main([
        "--file", str(path), "--title", "A third thing",
        "--priority", "high", "--dated", "09-09", "--dry-run",
    ])


def test_board_row_main_refuses_a_write_that_ate_a_bullet(tmp_path, monkeypatch):
    assert _damage_board_row(tmp_path, monkeypatch, _eat_a_bullet) == 1


def test_board_row_main_refuses_a_write_that_grew_a_row_from_nowhere(
    tmp_path, monkeypatch
):
    """`board_row` expects exactly +1, so a second new row is +2 and refused."""
    assert _damage_board_row(tmp_path, monkeypatch, _add_a_row) == 1


def test_board_row_main_refuses_a_write_that_moved_a_row_it_was_not_asked_about(
    tmp_path, monkeypatch
):
    """The other-rows half, driven through `main` for the same reason.

    This used to rename the other row instead, because
    `board_row.check_from_contents` compared only `title` and `status` on a
    pre-existing row where the five writers above compare the whole row
    dict. That narrowness is gone (Cycle 1317), so this runs the *same*
    `_move_another_row` damage as the parametrised test above -- and that
    damage now moves a rating rather than a status, which is a cell the old
    narrow guard could not see. One damage for all six writers, and it is
    the widening it fails on.
    """
    assert _damage_board_row(tmp_path, monkeypatch, _move_another_row) == 1


def test_untag_reads_the_document_once_per_version(tmp_path, monkeypatch):
    import tools.board_untag_project as module

    path = tmp_path / "issues.md"
    path.write_text(UNTAG_BOARD, encoding="utf-8")
    counts = _count_parses(module, monkeypatch)

    assert module.main(["--file", str(path), "--dry-run"]) == 0
    # Three before the split: the rule that picks the tagged rows parsed
    # `before` for itself, and then the guard parsed it again.
    assert counts == {"parse_board": 2, "parse_notes": 2}


def test_untag_guard_cannot_reach_a_document(tmp_path, monkeypatch):
    import tools.board_untag_project as module

    path = tmp_path / "issues.md"
    path.write_text(UNTAG_BOARD, encoding="utf-8")
    guard = module.check_from_contents

    def no_document_here(*a, **k):
        for name in ("parse_board", "parse_notes"):
            monkeypatch.setattr(module, name, _refuse)
        return guard(*a, **k)

    monkeypatch.setattr(module, "check_from_contents", no_document_here)
    assert module.main(["--file", str(path), "--dry-run"]) == 0


def _damage_untag(tmp_path, monkeypatch, damage):
    import tools.board_untag_project as module

    path = tmp_path / "issues.md"
    path.write_text(UNTAG_BOARD, encoding="utf-8")
    real = module.untag

    def damaged(markdown, board, numbers=None):
        written, moves, skipped = real(markdown, board, numbers)
        return (None if written is None else damage(written)), moves, skipped

    monkeypatch.setattr(module, "untag", damaged)
    return module.main(["--file", str(path), "--dry-run"])


def test_untag_main_refuses_a_write_that_ate_a_bullet(tmp_path, monkeypatch):
    assert _damage_untag(tmp_path, monkeypatch, _eat_a_bullet) == 1


def test_untag_main_refuses_a_write_that_moved_a_row_it_was_not_asked_about(
    tmp_path, monkeypatch
):
    """#100 is the row nothing asked about; #104 is the one carrying the tag."""

    def move_the_untouched_row(written):
        damaged = written.replace(
            "| Weekly work | 🟡 In progress | 08-24 |",
            "| Weekly work | ⚪ Backlog | 08-24 |",
        )
        assert damaged != written, "the fixture's first row is not in the written board"
        return damaged

    assert _damage_untag(tmp_path, monkeypatch, move_the_untouched_row) == 1


def _count_capture_reads(module, monkeypatch):
    """`board_capture` reads its bullets with `capture_entries`, not `parse_notes`.

    Same namespace argument as `_count_parses`: both names are bound into
    `tools.board_capture` by its own `from ... import`, so a counter on
    `nova_boards` would see the calls `add_row` and `set_row_status` make
    inside the write and none of the ones this test is about.
    """
    counts = {"parse_board": 0, "capture_entries": 0}
    for name in counts:
        real = getattr(module, name)

        def counting(markdown, _real=real, _name=name):
            counts[_name] += 1
            return _real(markdown)

        monkeypatch.setattr(module, name, counting)
    return counts


# `capture_entries` reads the bullets ABOVE the first heading and stops there,
# which is where the box he types into actually sits -- `BOARD`'s bullet is
# under `## Entries`, which is `parse_notes`' list, not his. So `board_capture`
# sees an empty box on the shared fixture and this one puts a bullet where the
# tool looks.
CAPTURE_BOARD = BOARD.replace(
    "---\n\n# Nova — Ideas",
    "---\n\n- Something he typed and nothing has boarded yet.\n\n# Nova — Ideas",
    1,
)


def _capture_argv(path):
    return [
        "--file", str(path), "--index", "0", "--priority", "high",
        "--dated", "09-09", "--dry-run",
    ]


def test_capture_reads_the_document_once_per_version(tmp_path, monkeypatch):
    """Five reads before the split: `before` three times, `after` twice.

    `promote` parsed the board for the project names and read the bullets
    for the span; `check` parsed and read both versions again; and `main`
    parsed `after` a third time to print the row it had just written.
    """
    import tools.board_capture as module

    path = tmp_path / "issues.md"
    path.write_text(CAPTURE_BOARD, encoding="utf-8")
    counts = _count_capture_reads(module, monkeypatch)

    assert module.main(_capture_argv(path)) == 0
    assert counts == {"parse_board": 2, "capture_entries": 2}


def test_capture_guard_cannot_reach_a_document(tmp_path, monkeypatch):
    import tools.board_capture as module

    path = tmp_path / "issues.md"
    path.write_text(CAPTURE_BOARD, encoding="utf-8")
    guard = module.check_from_contents

    def no_document_here(*a, **k):
        for name in ("parse_board", "capture_entries"):
            monkeypatch.setattr(module, name, _refuse)
        return guard(*a, **k)

    monkeypatch.setattr(module, "check_from_contents", no_document_here)
    assert module.main(_capture_argv(path)) == 0


def test_capture_promote_will_not_re_read_the_document_for_itself(
    tmp_path, monkeypatch
):
    """The board and the bullets are required arguments, not defaulted ones.

    A default that fell back to parsing `before` would put the second read
    straight back in and every other test here would still pass.
    """
    import inspect

    import tools.board_capture as module

    parameters = inspect.signature(module.promote).parameters
    for name in ("board", "entries"):
        assert parameters[name].default is inspect.Parameter.empty


def _damage_capture(tmp_path, monkeypatch, damage):
    import tools.board_capture as module

    path = tmp_path / "issues.md"
    path.write_text(CAPTURE_BOARD, encoding="utf-8")
    real = module.add_row

    def damaged(before, *a, **k):
        written, number = real(before, *a, **k)
        return (None if written is None else damage(written)), number

    monkeypatch.setattr(module, "add_row", damaged)
    return module.main(_capture_argv(path))


def test_capture_main_refuses_a_write_that_grew_a_row_from_nowhere(
    tmp_path, monkeypatch
):
    """Boarding is +1 row exactly, so a smuggled second row fails the count.

    `_eat_a_bullet` is deliberately not used here: it removes a bullet from
    `## Entries`, which is `parse_notes`' list, and `board_capture` reads the
    capture box with `capture_entries` and does not look at `## Entries` at
    all. That gap is real and is filed rather than closed here -- it changes
    what this guard refuses, not what it reads.
    """
    assert _damage_capture(tmp_path, monkeypatch, _add_a_row) == 1


def test_capture_main_refuses_a_write_that_moved_a_row_it_was_not_asked_about(
    tmp_path, monkeypatch
):
    """The damage moves a RATING, which the guard did not compare until now."""
    assert _damage_capture(tmp_path, monkeypatch, _move_another_row) == 1
