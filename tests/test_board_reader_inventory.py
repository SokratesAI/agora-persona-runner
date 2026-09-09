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
