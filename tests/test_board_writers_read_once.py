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

BOARD = """---
type: log
---

# Nova — Ideas

## Entries

- 2026-08-26 (Cycle 480) — a bullet nothing here may touch

## Board

| # | Item | Project | Milestone | Status | Updated | Priority | Size |
|---|------|---------|-----------|--------|---------|----------|------|
| [[#100 — Weekly work\\|100]] | Weekly work | Nova | Cost | 🟡 In progress | 08-24 | 🟠 High | 🅼 Medium |
| [[#104 — Metered API\\|104]] | Metered API | Nova | Cost | ⚪ Backlog | 08-24 | 🟠 High | 🅼 Medium |

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
OPEN_ROW = "| Metered API | Nova | Cost | ⚪ Backlog |"
MOVED_ROW = "| Metered API | Nova | Cost | 🟡 In progress |"


def _eat_a_bullet(written):
    damaged = written.replace(BULLET, "")
    assert damaged != written, "the fixture's bullet is not in the written board"
    return damaged


def _move_another_row(written):
    damaged = written.replace(OPEN_ROW, MOVED_ROW)
    assert damaged != written, "the fixture's second row is not in the written board"
    return damaged
