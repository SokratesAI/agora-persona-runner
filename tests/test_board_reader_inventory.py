"""The corrected coverage checklist for issue #203's reader migration."""
from pathlib import Path

from tools import board_reader_inventory as inv

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
    """The control for the test above: nova_site really does parse.

    It was nova_boards until #203 deleted the markdown `set_row_order`,
    then nova_site until the flip deleted its markdown fallback, and no
    live module is left that parses his boards -- so the control is a
    synthetic text now rather than a live module that can stop parsing."""
    text = ('board = parse_board(edvard_board_markdown(name))\n'
            'path = BOARD_PATHS[name]["edvard"]\n')
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
                "tools/project_trl.py"):
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
    found, refs, unreadable, untokenized, mine, vetoed = inv.scan(tmp_path)
    assert found == {"broken.py": ("parse_board",)}
    assert untokenized == ["broken.py"]
    assert inv.main(["--root", str(tmp_path)]) == 0
    assert "would not tokenize" in capsys.readouterr().out


def test_a_tokenizing_file_is_not_named_as_a_fallback(tmp_path, capsys):
    (tmp_path / "a.py").write_text("parse_board(x)")
    assert inv.scan(tmp_path)[3] == []
    inv.main(["--root", str(tmp_path)])
    assert "would not tokenize" not in capsys.readouterr().out


def test_assert_migrated_passes_on_the_live_tree(capsys):
    """#203's flip: nova_site was the last module reading his boards as
    markdown. `test_a_surviving_board_paths_read_does_not_block_migrated`
    is the control that the same call still answers 2 for a reader."""
    assert inv.main(["--assert-migrated"]) == 0
    assert "NOT MIGRATED" not in capsys.readouterr().out


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


HIS_ISSUES = "projects/sokrates/projects/nova/issues.md"
MY_ISSUES = "projects/sokrates/projects/agora/nova/resources/issues.md"


def test_the_two_path_sets_are_read_out_of_board_paths_and_do_not_overlap():
    """Spelling either set again here is how a fifth board file gets into
    one place and not the other."""
    assert HIS_ISSUES in inv.HIS_BOARD_PATHS
    assert MY_ISSUES in inv.MY_BOARD_PATHS
    assert not (inv.HIS_BOARD_PATHS & inv.MY_BOARD_PATHS)
    assert len(inv.HIS_BOARD_PATHS) == 2 and len(inv.MY_BOARD_PATHS) == 4


def test_a_concatenated_path_resolves_because_that_is_how_roll_health_spells_it():
    text = f'BASE = "projects/sokrates/projects/agora/nova/resources/"\n' \
           f'PAIRS = (BASE + "issues.md",)\n'
    assert inv.board_paths_named(text) == {MY_ISSUES}
    assert inv.reads_only_my_boards(text)


def test_naming_one_of_his_boards_is_a_blocker_even_beside_one_of_mine():
    text = f'A = "{MY_ISSUES}"\nB = "{HIS_ISSUES}"\n'
    assert inv.board_paths_named(text) == {MY_ISSUES, HIS_ISSUES}
    assert not inv.reads_only_my_boards(text)


def test_naming_no_board_path_at_all_is_a_blocker():
    """"I could not tell" and "it reads his board" must give the same
    answer, or the gate becomes a way of not being counted."""
    assert inv.board_paths_named("rows = parse_board(text)") == set()
    assert not inv.reads_only_my_boards("rows = parse_board(text)")
    assert not inv.reads_only_my_boards('p = BOARD_PATHS["issues"]["nova"]')


def test_an_unparseable_file_resolves_no_paths():
    assert inv.board_paths_named("def f(:\n") == set()
    assert not inv.reads_only_my_boards(f'def f(:\n  x = "{MY_ISSUES}"\n')


def test_roll_health_reads_only_my_boards_and_is_not_a_blocker(capsys):
    """Pinned against the live file: it parses markdown, and every board
    document it names is one of mine, so #203 does not move it."""
    text = (ROOT / "tools/roll_health.py").read_text(encoding="utf-8")
    assert inv.surfaces(text) == ("parse_board",)
    assert inv.reads_only_my_boards(text)
    assert "tools/roll_health.py" in inv.scan()[4]
    inv.main([])
    out = capsys.readouterr().out
    assert "read only MY OWN board files" in out
    assert "tools/roll_health.py" in out.split("read only MY OWN")[1]


def test_a_module_that_only_reads_my_boards_does_not_hold_the_gate(tmp_path,
                                                                   capsys):
    (tmp_path / "mine.py").write_text(f'p = "{MY_ISSUES}"\nparse_board(p)\n')
    assert inv.main(["--root", str(tmp_path), "--assert-migrated"]) == 0
    assert "MIGRATED — no module parses one of his boards" in capsys.readouterr().out
    (tmp_path / "his.py").write_text(f'p = "{HIS_ISSUES}"\nparse_board(p)\n')
    assert inv.main(["--root", str(tmp_path), "--assert-migrated"]) == 2
    assert "his.py" in capsys.readouterr().out


def test_board_put_does_not_exclude_my_boards_for_having_no_rows():
    """The stated reason was false -- my boards do have `## Board` tables --
    and a cycle acting on it would have seeded them into his record store.
    The exclusion is ownership, so it must hold for a board file of mine
    that parses to rows."""
    from agora_runner.nova_boards import parse_board
    from tools import board_put
    assert board_put.record_board(MY_ISSUES) is None
    assert board_put.record_board(HIS_ISSUES) == "issue"
    source = (ROOT / "tools/board_put.py").read_text(encoding="utf-8")
    assert "used to be false" in source, \
        "keep the correction, and keep the false reason quoted under it"
    assert "table, no rows" in source
    assert "35 rows" in source and "26 write-ups" in source
    rows = parse_board(
        "## Board\n\n"
        "| # | Item | Status | Updated | Priority |\n"
        "|---|------|--------|---------|---|\n"
        "| [[#1 \u2014 x|1]] | x | \u26aa Backlog | 09-10 | \u26aa Low |\n"
    )["items"]
    assert [r["number"] for r in rows] == [1]


def test_only_a_parser_can_be_excused_as_reading_my_boards(tmp_path, capsys):
    """`mine` is printed as a count out of the parsers, so a path-only
    module in it makes that sentence say more than it counted."""
    (tmp_path / "paths_only.py").write_text(f'p = "{MY_ISSUES}"\nBOARD_PATHS\n')
    found, refs, unreadable, untokenized, mine, vetoed = inv.scan(tmp_path)
    assert found == {"paths_only.py": ("BOARD_PATHS",)}
    assert mine == []
    inv.main(["--root", str(tmp_path)])
    assert "read only MY OWN" not in capsys.readouterr().out


# The excusals the path rule cannot resolve, ported back after the merge of
# main into #968 (cycle 1376) dropped them: a module handed its text at
# runtime names no path, so the path rule alone cannot excuse it.


def test_every_named_exemption_names_a_file_that_really_still_parses():
    """A stale exemption is a gate widened by accident."""
    for table in (inv.NOT_A_BOARD, inv.READS_MARKDOWN_BY_DESIGN):
        assert table, "an empty list needs no code path"
        for rel, reason in table.items():
            path = ROOT / rel
            assert path.exists(), f"{rel} is exempt and does not exist"
            assert inv.PARSES in inv.surfaces(path.read_text()), \
                f"{rel} is exempt from a parse gate and does not parse"
            assert reason.strip(), f"{rel} is exempt for no stated reason"


def test_the_two_exemption_lists_do_not_overlap():
    assert not (set(inv.NOT_A_BOARD) & set(inv.READS_MARKDOWN_BY_DESIGN))


def test_no_named_exemption_is_one_the_path_rule_already_makes():
    """A name on a module the path rule resolves is a second reason nobody
    needs, and the next edit to one of them diverges from the other."""
    for rel in inv.NOT_A_BOARD:
        assert not inv.reads_only_my_boards((ROOT / rel).read_text()), rel


def test_no_live_named_exemption_names_his_board():
    """The veto, on the real files: every NOT_A_BOARD entry is honest today."""
    for rel in inv.NOT_A_BOARD:
        assert not inv.names_his_board((ROOT / rel).read_text()), rel


def test_a_named_module_does_not_block_assert_migrated(tmp_path, capsys):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "board_row.py").write_text("parse_board(text)")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 module(s) touch a board, 1 of them by parsing markdown." in out
    assert inv.NOT_A_BOARD["tools/board_row.py"] in out
    (tmp_path / "tools" / "other.py").write_text("parse_board(text)")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 2


def test_a_named_module_that_names_his_board_is_vetoed(tmp_path, capsys):
    """A name must not outvote the one thing the path rule can measure."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "board_row.py").write_text(
        f'P = "{HIS_ISSUES}"\nparse_board(read(P))')
    assert inv.scan(tmp_path)[5] == ["tools/board_row.py"]
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "VETOED" in out
    line = next(l for l in out.split("\n") if l.startswith("NOT MIGRATED"))
    assert "tools/board_row.py" in line


def test_a_by_design_module_is_not_vetoed_for_naming_his_board(tmp_path):
    """Naming his board is the point of the migration's own comparison."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "board_migration_preflight.py").write_text(
        f'P = "{HIS_ISSUES}"\nparse_board(read(P))')
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 0


def test_the_gate_names_none_of_the_excused_modules(capsys):
    """On the live tree, so this is about the real exemptions.

    After #203's flip there is no NOT MIGRATED line at all, so the check is
    that every exempt module is still reported -- as excused, by name --
    rather than silently dropped from the report."""
    assert inv.main(["--assert-migrated"]) == 0
    out = capsys.readouterr().out
    assert "NOT MIGRATED" not in out
    for rel in (*inv.READS_MARKDOWN_BY_DESIGN, "tools/roll_health.py"):
        assert rel in out, rel


# The mirror surface: `nova_tickets`, which the parse_board grep cannot see.

def test_a_qualified_mirror_read_is_a_mirror_surface():
    assert inv.surfaces("rows = ticket_docs.read_rows(path)") == ("mirror",)
    assert inv.surfaces(
        "from agora_runner.ticket_docs import read_head") == ("mirror",)


def test_the_record_stores_own_read_rows_is_not_the_mirror():
    assert inv.surfaces("rows = board_store.read_rows('issue')") == ()
    assert inv.surfaces(
        "from agora_runner.board_store import read_rows") == ()
    text = (ROOT / "agora_runner" / "board_store.py").read_text(encoding="utf-8")
    assert "def read_rows(" in text, "board_store no longer has the collision"
    assert inv.MIRROR not in inv.surfaces(text, "agora_runner/board_store.py")


def test_the_mirror_is_reached_by_more_than_the_four_obvious_reads():
    assert inv.surfaces("v, why = ticket_docs.currency(path, rev)") == ("mirror",)
    assert inv.surfaces("r = ticket_docs.stored_source_rev(path)") == ("mirror",)


def test_every_spelling_of_the_import_reaches_the_same_module():
    for source in (
            "import agora_runner.ticket_docs\n"
            "rows = agora_runner.ticket_docs.read_rows(p)",
            "import agora_runner.ticket_docs as td\nrows = td.read_rows(p)",
            "from agora_runner import ticket_docs as td\nrows = td.read_rows(p)",
            "from agora_runner import ticket_docs\n"
            "rows = ticket_docs.read_rows(p)"):
        assert inv.surfaces(source) == ("mirror",), source


def test_a_file_that_will_not_parse_still_reports_its_mirror_read():
    assert inv.surfaces("x = = 1\nrows = ticket_docs.read_rows(p)") == ("mirror",)


def test_a_module_on_both_surfaces_is_counted_once(tmp_path, capsys):
    (tmp_path / "agora_runner").mkdir()
    (tmp_path / "agora_runner" / "nova_site.py").write_text(
        "rows = ticket_docs.read_rows(p)\nboard = parse_board(text)")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert out.count("agora_runner/nova_site.py") == 3, out
    assert "NOT MIGRATED — 1 module(s)" in out


def test_the_site_no_longer_reads_his_board_out_of_the_mirror():
    # Issue #203: the board page's fast path reads the record store now, so
    # nothing on the request path reads `nova_tickets`. A mirror read added
    # back to the site is a second source for one board, and it is a
    # regression this pins rather than a surface the gate would forgive.
    rel = "agora_runner/nova_site.py"
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert not inv.mirror_reads(text, rel), f"{rel} reads nova_tickets again"
    # The precondition: the detector still fires on the idiom the site used,
    # so a pass above is not a detector that can no longer see anything.
    assert inv.mirror_reads(
        "from agora_runner.ticket_docs import read_rows\nread_rows(p)", rel)


def test_the_mirror_module_counts_by_defining_the_api_not_by_its_name():
    """`ticket_docs` stays imported by `board_store` for credentials and
    HTTP, so it can only leave the count by no longer defining the read
    API -- which is what Cycle 1380 did. The precondition is that putting
    one definition back would count again, so the pass below is the
    deletion and not a detector that stopped looking."""
    text = (ROOT / inv.MIRROR_DEFINES).read_text(encoding="utf-8")
    assert not inv.mirror_reads(text, inv.MIRROR_DEFINES)
    for name in inv.MIRROR_READS:
        revived = text + f"\n\ndef {name}(path):\n    return None\n"
        assert inv.mirror_reads(revived, inv.MIRROR_DEFINES), name


def test_naming_the_mirror_in_prose_is_not_reading_it():
    assert inv.surfaces('"""ticket_docs.read_rows is what this replaces."""') == ()
    assert inv.surfaces("# ticket_docs.read_details used to answer here") == ()


def test_a_mirror_reader_blocks_assert_migrated(tmp_path, capsys):
    (tmp_path / "agora_runner").mkdir()
    reader = tmp_path / "agora_runner" / "nova_site.py"
    reader.write_text("rows = ticket_docs.read_rows(path)")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 2
    assert "nova_tickets" in capsys.readouterr().out
    reader.write_text("rows = board_records.contents('issue')")
    assert inv.main(["--assert-migrated", "--root", str(tmp_path)]) == 0
