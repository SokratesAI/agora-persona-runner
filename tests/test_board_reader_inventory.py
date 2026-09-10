"""The corrected coverage checklist for issue #203's reader migration."""
import re
from pathlib import Path

from tools import board_reader_inventory as inv

#: `board_row` on a word boundary, so `insert_board_row` and
#: `top_board_rows` do not read as calls to `tools/board_row.py`.
_BOARD_ROW_RE = re.compile(r"(?<!\w)board_row(?!\w)")

ROOT = Path(__file__).resolve().parent.parent


def test_a_parse_board_call_is_a_parse_surface():
    assert inv.surfaces("rows = nova_boards.parse_board(text)") == ("parse_board",)


def test_reading_board_paths_is_a_path_surface():
    assert inv.surfaces("path = BOARD_PATHS['issue']") == ("BOARD_PATHS",)


def test_parse_board_refs_is_not_a_board_reader():
    """Pinned against the live files, not a fixture — the spec's own grep
    lists all three of these and none of them touches a board."""
    for rel in ("agora_runner/nova_journal.py", "agora_runner/nova_plan.py",
                "tools/lint_entry.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "parse_board_refs" in text, f"{rel} no longer calls it"
        assert inv.surfaces(text) == (), f"{rel} read as a board reader"
        assert inv.refs_only(text), f"{rel} not reported as a false positive"


def test_the_live_definition_is_a_parse_surface():
    """The control for the test above: nova_boards really does parse."""
    text = (ROOT / "agora_runner/nova_boards.py").read_text(encoding="utf-8")
    assert inv.surfaces(text) == ("parse_board", "BOARD_PATHS")


def test_scan_leaves_out_tests_unless_asked(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tools" / "a.py").write_text("parse_board(x)")
    (tmp_path / "tests" / "test_a.py").write_text("parse_board(x)")
    assert set(inv.scan(tmp_path)[0]) == {"tools/a.py"}
    assert set(inv.scan(tmp_path, include_tests=True)[0]) == {
        "tools/a.py", "tests/test_a.py"}


def test_scan_does_not_count_itself():
    """It is the instrument, not a reader — and its own file is the
    smallest proof that a text match is not a call: it names both
    surfaces on nearly every line and calls neither."""
    found = inv.scan()[0]
    text = Path(inv.__file__).read_text(encoding="utf-8")
    assert "tools/board_reader_inventory.py" not in found
    assert "parse_board" in text and "BOARD_PATHS" in text
    assert inv.surfaces(text) == ()


def test_a_docstring_mention_is_not_a_call():
    """Pinned against the live modules, not a fixture. Both are the #203
    replacement path: they describe `parse_board` at length because they
    exist to replace it, and neither one calls it. Read as text they were
    reported as readers still to convert, which is a migration that can
    never finish."""
    for rel in ("agora_runner/board_view.py", "agora_runner/board_document.py",
                "agora_runner/ticket_docs.py", "tools/project_trl.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "parse_board" in text, f"{rel} no longer mentions it"
        assert inv.surfaces(text) == (), f"{rel} read as a board reader"


def test_a_comment_mention_of_a_path_is_not_a_read():
    text = (ROOT / "tools/name_scan.py").read_text(encoding="utf-8")
    assert "BOARD_PATHS" in text
    assert inv.surfaces(text) == ()


def test_code_survives_the_comment_strip():
    """The control for the two above: the same name in real code counts."""
    assert inv.surfaces("rows = parse_board(t)  # parse_board") == ("parse_board",)
    assert inv.surfaces('"""parse_board"""\nrows = parse_board(t)') == (
        "parse_board",)
    assert inv.surfaces('BOARD_PATHS  # \'parse_board\'') == ("BOARD_PATHS",)


def test_parse_board_refs_in_a_comment_is_not_a_false_positive():
    """`refs_only` reads code too, so a module that merely writes the name
    in prose is not announced as a corrected false positive."""
    assert inv.refs_only("x = parse_board_refs(e)")
    assert not inv.refs_only("# parse_board_refs is not a board reader")


def test_a_file_that_will_not_tokenize_falls_back_and_says_so(tmp_path,
                                                              capsys):
    """Over-reporting a reader is recoverable; missing one is not. So a
    broken file is matched as raw text — and named, because that result
    may be a comment."""
    (tmp_path / "broken.py").write_text("def f(:\n  # parse_board\n")
    assert not inv.tokenizes((tmp_path / "broken.py").read_text())
    found, refs, unreadable, untokenized = inv.scan(tmp_path)
    assert found == {"broken.py": ("parse_board",)}
    assert untokenized == ["broken.py"]
    assert inv.main(["--root", str(tmp_path)]) == 0
    assert "would not tokenize" in capsys.readouterr().out


def test_a_tokenizing_file_is_not_named_as_a_fallback(tmp_path, capsys):
    (tmp_path / "a.py").write_text("parse_board(x)")
    assert inv.scan(tmp_path)[3] == []
    inv.main(["--root", str(tmp_path)])
    assert "would not tokenize" not in capsys.readouterr().out


def test_assert_migrated_raises_on_the_live_tree(capsys):
    assert inv.main(["--assert-migrated"]) == 2
    out = capsys.readouterr().out
    assert "NOT MIGRATED" in out
    assert "agora_runner/nova_boards.py" in out


def test_a_surviving_board_paths_read_does_not_block_migrated(tmp_path):
    """The spec keeps a generated issues.md from day one, so the view writer
    still has to know where the file lives."""
    (tmp_path / "view.py").write_text("open(BOARD_PATHS['issue'], 'w')")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 0
    (tmp_path / "reader.py").write_text("parse_board(text)")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 2


def test_an_unreadable_file_is_no_instrument(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("parse_board(x)")
    (tmp_path / "b.py").write_text("x = 1")
    real = Path.read_text

    def boom(self, *args, **kwargs):
        if self.name == "b.py":
            raise OSError("nope")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    assert inv.main(["--root", str(tmp_path)]) == 1


def test_an_f_string_is_prose_but_its_holes_are_code():
    """Python 3.12 stopped emitting an f-string as one STRING token, so a
    strip that only drops STRING leaves the literal text behind — this
    module's own error message says "still call parse_board" and read as
    a call until the tokenizer's f-string types were added."""
    assert inv.surfaces('msg = f"still call parse_board: {n}"') == ()
    assert inv.surfaces('msg = f"rows: {parse_board(t)}"') == ("parse_board",)


# --- A `parse_board` call is not always one of the owner's boards. ---------
#
# `--assert-migrated` is the gate that decides when the switchover branch
# leaves draft, and it was unreachable: `tools/roll_health.py` parses the
# board *shape* out of Nova's own capture files, which `board_migrate` never
# migrates, so it must keep calling `parse_board` after the migration is
# finished. Found cycle 1303.


def test_the_exemption_names_a_file_that_really_still_parses():
    """A stale exemption is a gate widened by accident.

    If a listed module is converted or deleted, its entry has to go with it
    — otherwise the list keeps excusing a name that no longer means
    anything, and the next module to take that path is excused silently.
    """
    assert inv.NOT_A_BOARD, "an empty list needs no code path"
    for rel, reason in inv.NOT_A_BOARD.items():
        path = ROOT / rel
        assert path.exists(), f"{rel} is exempt and does not exist"
        assert inv.PARSES in inv.surfaces(path.read_text()), \
            f"{rel} is exempt from a parse gate and does not parse"
        assert reason.strip(), f"{rel} is exempt for no stated reason"


def test_roll_health_reads_only_novas_own_capture_files():
    """The evidence under the exemption, not a restatement of it.

    Its `PAIRS` is hardcoded, so this is checkable rather than assumed: if
    anything ever points that tool at one of the owner's two boards, the
    exemption stops being true and this fails before the gate goes quiet.
    """
    from agora_runner.nova_boards import BOARD_PATHS
    from tools import roll_health

    owners = {paths["edvard"] for paths in BOARD_PATHS.values()}
    novas = {p for paths in BOARD_PATHS.values()
             for key, p in paths.items() if key != "edvard"}
    read = {path for pair in roll_health.PAIRS for path in pair}
    assert read, "no paths is not proof of the right paths"
    assert read <= novas
    assert not (read & owners)


def test_an_exempt_module_does_not_block_assert_migrated(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "roll_health.py").write_text("parse_board(text)")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 0
    (tmp_path / "tools" / "other.py").write_text("parse_board(text)")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 2


def test_the_exempt_module_is_still_reported_as_a_parser(tmp_path, capsys):
    """Excused from the gate, never hidden from the report."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "roll_health.py").write_text("parse_board(text)")
    inv.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "parses" in out
    assert "tools/roll_health.py" in out
    assert "1 module(s) touch a board, 1 of them by parsing markdown." in out
    assert inv.NOT_A_BOARD["tools/roll_health.py"] in out


def test_the_gate_does_not_name_an_exempt_module(capsys):
    """On the live tree, so this is about the real exemption."""
    assert inv.main(["--assert-migrated"]) == 2
    line = next(l for l in capsys.readouterr().out.split("\n")
                if l.startswith("NOT MIGRATED"))
    assert "tools/roll_health.py" not in line
    assert "agora_runner/nova_boards.py" in line


# Cycle 1322: the gate was unreachable in three more places, and the three
# are two different reasons. `roll_done_details` is `roll_health`'s own
# roller for `roll_health.PAIRS`, so it is the same exemption. `board_migrate`
# and `board_migration_preflight` parse the owner's real boards and must,
# because the migration is a `parse_board` call by definition -- a second
# list, because the reason is what the next cycle reads.


def test_the_by_design_exemption_names_a_file_that_really_still_parses():
    """Same integrity check as `NOT_A_BOARD`, on the second list."""
    assert inv.READS_MARKDOWN_BY_DESIGN, "an empty list needs no code path"
    for rel, reason in inv.READS_MARKDOWN_BY_DESIGN.items():
        path = ROOT / rel
        assert path.exists(), f"{rel} is exempt and does not exist"
        assert inv.PARSES in inv.surfaces(path.read_text()), \
            f"{rel} is exempt from a parse gate and does not parse"
        assert reason.strip(), f"{rel} is exempt for no stated reason"


def test_the_two_exemption_lists_do_not_overlap():
    """One module, one reason -- a name in both is a reason nobody chose."""
    assert not (set(inv.NOT_A_BOARD) & set(inv.READS_MARKDOWN_BY_DESIGN))


def test_roll_done_details_is_only_ever_pointed_at_novas_own_files():
    """The evidence under its exemption, not a restatement of it.

    Its paths come from `--live`/`--archive` at runtime, so nothing static
    can read them off the module. What is checkable is who calls it: if
    `roll_health` is the only caller in this tree, then the only paths it is
    handed are `roll_health.PAIRS`, which the test above pins to Nova's own
    files. A second caller appearing fails here rather than widening the
    gate in silence.

    Through `code_only`, because the inventory names this module inside the
    exemption dict: a name in a string is documentation, not a call, and the
    same distinction is what stops the scanner reading a docstring as a
    reader.
    """
    assert "tools/roll_done_details.py" in inv.NOT_A_BOARD, \
        "the evidence has to be tied to the entry it excuses"
    callers = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "tools/roll_done_details.py")):
            continue
        if any(part in inv.SKIP_DIRS for part in path.parts):
            continue
        if "roll_done_details" in inv.code_only(path.read_text()):
            callers.add(rel)
    assert callers == {"tools/roll_health.py"}, sorted(callers)


def test_the_record_store_has_no_board_for_novas_own_two_files():
    """The half of board_row's exemption that is checkable in code.

    `BOARD_PATHS` keeps two documents per kind -- the owner's and Nova's --
    and the record store models one of them. `board_document.BOARDS` names
    the owner's two and `document_id` mints one key range for them, so a
    tool pointed at the `nova` path has no board name to be converted to.
    If Nova's own boards ever get an id space, this fails and the exemption
    is reconsidered rather than left standing.
    """
    from agora_runner import board_document, nova_boards

    for kind in ("issues", "ideas"):
        paths = nova_boards.BOARD_PATHS[kind]
        assert paths["nova"] != paths["edvard"], \
            f"{kind}: Nova's board and the owner's are the same document"
    assert set(board_document.BOARDS) == {"issue", "idea"}, \
        "a third board name means the store may now hold Nova's own"
    assert board_document.document_id("issue", 41) == "board:issue:41", \
        "one key range per kind, so the owner's board and Nova's collide"


def test_board_row_has_no_caller_in_this_tree():
    """The other half: nothing here can point that button at his board.

    Same shape as `roll_done_details` above and the same limit -- `--file`
    is a runtime path, so nothing static reads the target off the module.
    What is checkable is that this tree hands it none: its only caller is
    `prompt.md` step 6, which passes Nova's own `resources/issues.md`. A
    second caller appearing in code fails here rather than widening the
    gate in silence.

    It does NOT prove the tool could not be run against the owner's file by
    hand. Nothing can; that is why the entry carries a reason in prose.

    The name is matched on a boundary rather than as a substring, because
    `nova_idea_pool.insert_board_row` and `top_board_rows` both contain it
    and neither is a call to this module. A bare `in` reported the first of
    those as a caller on the run that wrote this test.
    """
    assert "tools/board_row.py" in inv.NOT_A_BOARD, \
        "the evidence has to be tied to the entry it excuses"
    callers = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "tools/board_row.py")):
            continue
        if any(part in inv.SKIP_DIRS for part in path.parts):
            continue
        if _BOARD_ROW_RE.search(inv.code_only(path.read_text(encoding="utf-8"))):
            callers.add(rel)
    assert callers == set(), sorted(callers)


def test_the_migration_tools_are_the_records_side_of_the_seam():
    """The evidence under the second list: they write or check records.

    A module that only reads a board cannot be excused this way -- the
    excuse is that it produces the record set the readers move to, so it
    has to hold both shapes at once.
    """
    assert set(inv.READS_MARKDOWN_BY_DESIGN) == {
        "tools/board_migrate.py", "tools/board_migration_preflight.py"}, \
        "the evidence has to be tied to the entries it excuses"
    for rel in inv.READS_MARKDOWN_BY_DESIGN:
        text = (ROOT / rel).read_text()
        assert "board_store" in text, f"{rel} touches no record store"


def test_the_gate_names_none_of_the_four_excused_modules(capsys):
    """On the live tree, so this is about the real exemptions."""
    assert inv.main(["--assert-migrated"]) == 2
    line = next(l for l in capsys.readouterr().out.split("\n")
                if l.startswith("NOT MIGRATED"))
    for rel in (*inv.NOT_A_BOARD, *inv.READS_MARKDOWN_BY_DESIGN):
        assert rel not in line, f"{rel} is excused and still blocks the gate"
    assert "agora_runner/ticket_store.py" in line, \
        "ticket_store leaves the count by being deleted, not excused"


def test_a_by_design_module_does_not_block_assert_migrated(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "board_migrate.py").write_text("parse_board(text)")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 0
    (tmp_path / "tools" / "other.py").write_text("parse_board(text)")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 2


def test_a_by_design_module_is_still_reported_as_a_parser(tmp_path, capsys):
    """Excused from the gate, never hidden from the report."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "board_migrate.py").write_text("parse_board(text)")
    inv.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "parses" in out
    assert "tools/board_migrate.py" in out
    assert inv.READS_MARKDOWN_BY_DESIGN["tools/board_migrate.py"] in out
