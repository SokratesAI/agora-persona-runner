"""`tools.goal_baseline` -- write a dated baseline into `project-goals.md`."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools import goal_baseline as gb
from agora_runner.project_goals import parse_project_goals


def _doc(*blocks):
    return "\n".join(("# Goals", "", "## Nova the app", "") + blocks) + "\n"


def _kr(identifier, now="3", extra=()):
    lines = [f"id: {identifier}", f"name: {identifier}",
             "measure: things", f"now: {now}", "target: 0",
             "direction: down", "status: discussing"]
    return "\n".join(["```key-result"] + lines + list(extra) + ["```", ""])


def test_writes_a_dated_baseline_for_every_numeric_now():
    text = _doc(_kr("a-kr", now="3"), _kr("b-kr", now="7.4"))
    writes, skips = gb.pending(parse_project_goals(text))
    assert [w[1] for w in writes] == ["a-kr", "b-kr"]
    assert skips == []
    after, error = gb.apply(text, writes, "2026-09-16")
    assert error is None
    rows = {r["id"]: r for r in
            parse_project_goals(after)["nova the app"]["keyResults"]}
    assert rows["a-kr"]["baseline"] == "3 (2026-09-16)"
    assert rows["b-kr"]["baseline"] == "7.4 (2026-09-16)"


def test_a_blank_now_is_skipped_rather_than_baselined_as_nothing():
    # The failure this guards: draining the list by recording nothing. Four
    # live key results have no instrument, so `now` is empty, and a baseline
    # of "" would read as present to `key_results_without_baseline`.
    text = _doc(_kr("blank-kr", now=""), _kr("words-kr", now="not yet"))
    writes, skips = gb.pending(parse_project_goals(text))
    assert writes == []
    assert [s[1] for s in skips] == ["blank-kr", "words-kr"]


def test_a_baseline_already_written_is_left_exactly_as_it_stands():
    text = _doc(_kr("a-kr", extra=["baseline: 9 (2026-09-01)"]))
    writes, skips = gb.pending(parse_project_goals(text))
    assert (writes, skips) == ([], [])
    after, error = gb.apply(text, writes, "2026-09-16")
    assert (after, error) == (text, None)


def test_a_struck_key_result_is_not_asked_for_a_baseline():
    text = _doc(_kr("dead-kr").replace("status: discussing", "status: struck"))
    assert gb.pending(parse_project_goals(text)) == ([], [])


def test_verify_refuses_when_anything_but_a_baseline_moved():
    before = _doc(_kr("a-kr", now="3"))
    writes, _ = gb.pending(parse_project_goals(before))
    after, _ = gb.apply(before, writes, "2026-09-16")
    assert gb.verify(before, after, 1) is None
    tampered = after.replace("now: 3", "now: 0")
    assert "something other than a baseline changed" in gb.verify(
        tampered, after, 1)
    assert "against the" in gb.verify(before, after, 2)


def test_verify_does_not_strip_a_baseline_the_document_already_had():
    # `_INSERTED_RE` is the inverse of what `apply` writes, so an undated
    # baseline already in the document must survive the strip -- otherwise
    # verification would silently accept its deletion.
    before = _doc(_kr("a-kr", now="3", extra=["baseline: 9"]),
                  _kr("b-kr", now="5"))
    writes, _ = gb.pending(parse_project_goals(before))
    after, _ = gb.apply(before, writes, "2026-09-16")
    assert gb.verify(before, after, len(writes)) is None


def test_apply_refuses_an_id_it_cannot_address():
    text = _doc(_kr("a-kr"))
    after, error = gb.apply(text, [("Nova", "ghost-kr", "3")], "2026-09-16")
    assert after is None
    assert "ghost-kr" in error


def test_main_edits_the_file_in_place_and_reports(tmp_path, capsys):
    path = tmp_path / "project-goals.md"
    path.write_text(_doc(_kr("a-kr"), _kr("blank-kr", now="")),
                    encoding="utf-8")
    assert gb.main(["--goals", str(path), "--date", "2026-09-16"]) == 0
    out = capsys.readouterr().out
    assert "BASELINED 1 key result(s), 1 left without one" in out
    assert "baseline: 3 (2026-09-16)" in path.read_text(encoding="utf-8")


def test_main_refuses_a_date_that_is_not_a_date(tmp_path, capsys):
    path = tmp_path / "project-goals.md"
    path.write_text(_doc(_kr("a-kr")), encoding="utf-8")
    assert gb.main(["--goals", str(path), "--date", "today"]) == 2
    assert "baseline" not in path.read_text(encoding="utf-8")


def test_print_leaves_the_file_alone(tmp_path, capsys):
    path = tmp_path / "project-goals.md"
    path.write_text(_doc(_kr("a-kr")), encoding="utf-8")
    assert gb.main(["--goals", str(path), "--date", "2026-09-16",
                    "--print"]) == 0
    assert "baseline" not in path.read_text(encoding="utf-8")
    assert "baseline: 3 (2026-09-16)" in capsys.readouterr().out


def test_a_second_run_over_a_document_this_tool_already_baselined():
    # The strip-based check refused here: it took out the baselines written
    # on an earlier day as well, so a correct run that wrote nothing looked
    # like 26 unexplained insertions.
    first = _doc(_kr("a-kr", now="3"))
    writes, _ = gb.pending(parse_project_goals(first))
    after, _ = gb.apply(first, writes, "2026-09-16")
    assert gb.verify(first, after, len(writes)) is None
    again, skips = gb.pending(parse_project_goals(after))
    assert (again, skips) == ([], [])
    assert gb.verify(after, after, 0) is None


def test_a_deleted_line_is_refused_and_named():
    before = _doc(_kr("a-kr", now="3"))
    after = before.replace("measure: things\n", "")
    assert "something other than a baseline changed" in gb.verify(
        before, after, 0)
