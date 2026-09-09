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
    """Its own docstring names both surfaces; counting it would report a
    reader that does not exist."""
    found = inv.scan()[0]
    assert "tools/board_reader_inventory.py" not in found
    assert inv.surfaces(Path(inv.__file__).read_text(encoding="utf-8"))


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
