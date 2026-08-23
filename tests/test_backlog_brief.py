"""Tests for `tools/backlog_brief.py`.

The one that matters is `test_order_is_computed_not_positional`: the live
files read newest-first today, so a naive `notes[:limit]` passes every
other test in this file. That test feeds it a file in the *old* mixed
append order `parse_notes` documents and asserts the brief still returns
the newest entries -- which is the only way to catch the failure that
would otherwise appear silently the next time something appends the old
way, returning the oldest forty entries with no symptom at all.
"""

import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.backlog_brief import brief, head_section, render, retired_count, main


def _file(entries, head="- live friction one", retired=()):
    body = "\n".join(f"- {e}" for e in entries)
    out = f"# Nova — Issues\n\n{head}\n\n## Entries\n\n{body}\n"
    if retired:
        out += "\n## Retired\n\n" + "\n".join(f"- {r}" for r in retired) + "\n"
    return out


def test_keeps_newest_and_counts_the_rest():
    md = _file([f"2026-08-{d:02d} (Cycle {d}) — note {d}" for d in range(1, 21)])
    data = brief(md, limit=5)
    assert len(data["kept"]) == 5
    assert data["dropped"] == 15
    assert data["total"] == 20
    assert [n["date"] for n in data["kept"]] == [
        "2026-08-20", "2026-08-19", "2026-08-18", "2026-08-17", "2026-08-16"]
    assert data["oldest_kept"] == "2026-08-16"
    assert data["newest_dropped"] == "2026-08-15"


def test_order_is_computed_not_positional():
    """The two append conventions, in one file, as `parse_notes` describes.

    Newest-first at the top and oldest-first at the bottom, so the genuinely
    newest material is at *both* ends. A positional slice takes the top five
    and misses `2026-08-30`, which sits last.
    """
    md = _file([
        "2026-08-12 (Cycle 12) — descending stream",
        "2026-08-11 (Cycle 11) — descending stream",
        "2026-08-10 (Cycle 10) — descending stream",
        "2026-08-01 (Cycle 1) — ascending stream",
        "2026-08-02 (Cycle 2) — ascending stream",
        "2026-08-30 (Cycle 30) — newest of all, appended at the end",
    ])
    kept = [n["date"] for n in brief(md, limit=3)["kept"]]
    assert kept == ["2026-08-30", "2026-08-12", "2026-08-11"]


def test_undated_entries_sort_last_and_are_counted():
    md = _file([
        "2026-08-05 (Cycle 5) — dated",
        "no marker at all, just prose",
        "2026-08-06 (Cycle 6) — dated",
    ])
    data = brief(md, limit=2)
    assert [n["date"] for n in data["kept"]] == ["2026-08-06", "2026-08-05"]
    assert data["dropped"] == 1
    assert data["undated"] == 1
    assert "1 of them undated" in render("f", data, 2)


def test_limit_zero_keeps_everything():
    md = _file([f"2026-08-{d:02d} (Cycle {d}) — note" for d in range(1, 8)])
    data = brief(md, limit=0)
    assert len(data["kept"]) == 7
    assert data["dropped"] == 0
    assert "NOT SHOWN" not in render("f", data, 0)


def test_head_section_is_kept_verbatim_and_retired_is_only_counted():
    md = _file(["2026-08-05 (Cycle 5) — entry"],
               head="- current friction\n- second friction",
               retired=["old thing", "older thing"])
    assert head_section(md) == "- current friction\n- second friction"
    assert retired_count(md) == 2
    text = render("resources/issues.md", brief(md, limit=10), 10)
    assert "current friction" in text
    assert "old thing" not in text
    assert "NOT SHOWN: 2 entries under ## Retired." in text


def test_render_names_the_boundary_it_truncated_at():
    """A brief that hides its own truncation is the failure this guards."""
    md = _file([f"2026-08-{d:02d} (Cycle {d}) — note" for d in range(1, 11)])
    text = render("resources/ideas.md", brief(md, limit=4), 4)
    assert "-- newest 4 of 10 entries --" in text
    assert "NOT SHOWN: 6 older entries" in text
    assert "newest dropped dated 2026-08-06" in text
    assert "--limit 8" in text


def test_local_overrides_bypass_the_vault(tmp_path, capsys):
    """`--issues`/`--ideas` must not shell out -- this is how CI runs it."""
    issues = tmp_path / "i.md"
    ideas = tmp_path / "d.md"
    issues.write_text(_file(["2026-08-09 (Cycle 9) — an issue note"]))
    ideas.write_text(_file(["2026-08-08 (Cycle 8) — an idea note"]))
    code = main(["--issues", str(issues), "--ideas", str(ideas), "--limit", "5"])
    out = capsys.readouterr().out
    assert code == 0
    assert "an issue note" in out and "an idea note" in out
    assert "resources/issues.md" in out and "resources/ideas.md" in out


def test_missing_vault_file_is_loud_and_non_zero(monkeypatch, tmp_path, capsys):
    """`vault_tool.py get` exits 0 on a missing file; a quiet miss here would
    print a brief that reads as 'nothing open', which is the most reassuring
    possible way to be wrong."""
    import tools.backlog_brief as mod
    monkeypatch.setattr(mod, "_fetch", lambda path: None)
    ideas = tmp_path / "d.md"
    ideas.write_text(_file(["2026-08-08 (Cycle 8) — an idea note"]))
    code = main(["--ideas", str(ideas)])
    captured = capsys.readouterr()
    assert code == 1
    assert "COULD NOT READ" in captured.err
    assert mod.ISSUES_PATH in captured.err


def test_runs_as_a_script():
    """Matches tests/test_tools_run_as_scripts.py's contract."""
    root = pathlib.Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, str(root / "tools" / "backlog_brief.py"),
                           "--help"], capture_output=True, text=True)
    assert done.returncode == 0
    assert "--limit" in done.stdout
