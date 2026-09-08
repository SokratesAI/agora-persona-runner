"""The check that says the browser environment is gone before a cycle needs it.

Every case builds a real directory on disk and asks the real
`see_page.missing_pieces` about it. Nothing here re-lists the four pieces:
a test that spelled the rule out again would pass against a check that had
drifted from `bootstrap.sh`, which is the failure the module is written to
avoid.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import browser_env_health as beh
from tools.see_page import missing_pieces


def complete_root(tmp_path):
    """A root that `missing_pieces` has nothing to say about.

    Built by asking the rule what is missing and laying that down, so it
    stays complete if a fifth piece is ever added.
    """
    root = tmp_path / "nova-browser"
    root.mkdir()
    (root / "libdirs.txt").write_text("/lib\n")
    (root / "fontconf").mkdir()
    (root / "fontconf" / "fonts.conf").write_text("<fontconfig/>")
    (root / "browsers" / "chromium-1234").mkdir(parents=True)
    (root / "node_modules" / "playwright-core").mkdir(parents=True)
    assert missing_pieces(root) == [], "the fixture no longer satisfies the rule"
    return root


def test_a_complete_root_is_clean(tmp_path):
    status, headline, detail = beh.verdict(complete_root(tmp_path))
    assert status == 0
    assert headline == ""
    assert detail == []


def test_a_root_that_does_not_exist_is_absent_not_incomplete(tmp_path):
    status, headline, detail = beh.verdict(tmp_path / "never-built")
    assert status == 2
    assert "ABSENT" in headline
    assert "INCOMPLETE" not in headline
    assert any(beh.REBUILD in line for line in detail)


def test_a_half_built_root_is_incomplete_and_names_every_missing_piece(tmp_path):
    root = complete_root(tmp_path)
    (root / "libdirs.txt").unlink()
    (root / "fontconf" / "fonts.conf").unlink()
    status, headline, detail = beh.verdict(root)
    assert status == 2
    assert "INCOMPLETE" in headline
    assert "ABSENT" not in headline
    # The wording comes from `missing_pieces`, so this asserts the check
    # passes the rule's own answer through rather than paraphrasing it.
    for piece in missing_pieces(root):
        assert piece in detail
    # An incomplete root has to carry the rebuild command too, not only the
    # absent one -- they are two branches and each can lose it separately.
    assert any(beh.REBUILD in line for line in detail)


def test_absent_and_incomplete_do_not_share_a_headline(tmp_path):
    absent = beh.verdict(tmp_path / "never-built")[1]
    root = complete_root(tmp_path)
    (root / "libdirs.txt").unlink()
    incomplete = beh.verdict(root)[1]
    assert absent != incomplete


def test_a_root_that_is_a_file_cannot_be_read_and_never_reads_as_clean(tmp_path):
    notadir = tmp_path / "nova-browser"
    notadir.write_text("this is not a directory")
    status, headline, _ = beh.verdict(notadir)
    assert status == 1
    assert "CANNOT READ" in headline


def test_required_count_is_asked_of_the_rule_not_written_down(tmp_path, monkeypatch):
    """The printed count has to move when the rule moves.

    Comparing it against the real `missing_pieces` proves nothing on its own:
    both sides are 4 today, so a literal `4` in the module would pass. The
    rule is replaced with one of a different size, which is the only thing
    that separates asking from remembering.
    """
    assert beh.required_count() == len(missing_pieces(tmp_path / "empty"))
    monkeypatch.setattr(beh, "missing_pieces", lambda root: ["a", "b", "c"])
    assert beh.required_count() == 3


def test_the_report_names_the_root_and_carries_a_count(tmp_path, capsys, monkeypatch):
    root = complete_root(tmp_path)
    monkeypatch.setenv("NOVA_BROWSER_ROOT", str(root))
    assert beh.main() == 0
    out = capsys.readouterr().out
    assert str(root) in out
    # preflight's summary_line takes the last line carrying a digit, so a
    # sweep line with no count collapses to the NOT JUDGED footnote instead.
    # Asserting "some digit" is not enough: tmp_path carries digits of its
    # own, so the count has to be looked for where it is written.
    assert f"the {beh.required_count()} piece(s)" in out
    assert "NOT JUDGED" in out


@pytest.mark.parametrize("case,expected", [("never-built", 2), ("complete", 0)])
def test_exit_code_reaches_the_process(tmp_path, case, expected):
    root = complete_root(tmp_path) if case == "complete" else tmp_path / "never-built"
    proc = subprocess.run(
        [sys.executable, "-m", "tools.browser_env_health"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**__import__("os").environ, "NOVA_BROWSER_ROOT": str(root)},
        capture_output=True, text=True,
    )
    assert proc.returncode == expected, proc.stdout + proc.stderr
