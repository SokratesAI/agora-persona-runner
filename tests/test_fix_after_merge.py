"""Tests for tools/fix_after_merge.py.

The two failures that would make the rate a lie: counting a planned follow-up
("slice 2 of #n") as a repair, which is what the first cut of the tool did,
and counting a PR whose 48 hours are not over yet as one nothing repaired.
"""

import datetime
import json
import subprocess

from tools import fix_after_merge as fam

T0 = datetime.datetime(2026, 9, 1, 12, tzinfo=datetime.timezone.utc)


def pr(number, title, hours, files, body="", repo="r"):
    return {"repo": repo, "number": number, "title": title, "body": body,
            "merged": T0 + datetime.timedelta(hours=hours), "files": set(files)}


def test_named_repair_is_floor_and_ceiling():
    prs = [pr(1, "Add the thing", 0, ["a.py"]),
           pr(2, "Fix the thing from #1", 3, ["b.py"])]
    got = fam.pair_up(prs, 48)[("r", 1)]
    assert got["floor"]["number"] == 2 and got["ceiling"]["number"] == 2


def test_named_follow_up_without_repair_word_is_only_came_back():
    prs = [pr(1, "Slice 1", 0, ["a.py"]), pr(2, "Slice 2 of #1", 3, ["a.py"])]
    got = fam.pair_up(prs, 48)[("r", 1)]
    assert got["came_back"]["number"] == 2
    assert got["floor"] is None and got["ceiling"] is None


def test_unnamed_repair_on_shared_file_is_ceiling_only():
    prs = [pr(1, "Add", 0, ["a.py", "b.py"]), pr(2, "Fix a crash", 5, ["b.py"])]
    got = fam.pair_up(prs, 48)[("r", 1)]
    assert got["floor"] is None and got["ceiling"]["number"] == 2


def test_repair_on_other_file_or_outside_window_or_other_repo_is_nothing():
    prs = [pr(1, "Add", 0, ["a.py"]),
           pr(2, "Fix c", 1, ["c.py"]),
           pr(3, "Fix #1", 49, ["a.py"]),
           pr(4, "Fix #1", 2, ["a.py"], repo="other")]
    got = fam.pair_up(prs, 48)[("r", 1)]
    assert got == {"floor": None, "ceiling": None, "came_back": None}


def test_number_match_is_whole_and_not_another_repos_ref():
    prs = [pr(12, "Add", 0, ["a.py"]),
           pr(13, "Fix #123 and org/repo#12", 1, ["z.py"])]
    assert fam.pair_up(prs, 48)[("r", 12)]["came_back"] is None


def test_revert_by_title_counts_without_a_number():
    prs = [pr(1, "Move the dial", 0, ["a.py"]),
           pr(2, 'Revert "Move the dial"', 1, ["x.py"])]
    assert fam.pair_up(prs, 48)[("r", 1)]["floor"]["number"] == 2


def test_open_window_is_left_out_of_the_rate():
    prs = [pr(1, "Old", 0, ["a.py"]), pr(2, "Fix #1", 1, ["a.py"]),
           pr(3, "New", 60, ["a.py"])]
    s = fam.summarise(prs, fam.pair_up(prs, 48), T0 + datetime.timedelta(hours=70), 48)
    assert s["judged"] == 2 and s["too_recent"] == 1
    assert s["floor"] == 1 and s["ceiling"] == 1


def _runner(pages, repos=("r",)):
    def run(cmd, **kw):
        if cmd[1:3] == ["repo", "list"]:
            out = json.dumps([{"name": n} for n in repos])
        else:
            out = json.dumps(pages.pop(0))
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def _node(number, title, merged, files):
    return {"number": number, "title": title, "body": "", "mergedAt": merged,
            "updatedAt": merged, "files": {"nodes": [{"path": f} for f in files]}}


def test_main_end_to_end(capsys):
    page = {"data": {"repository": {"pullRequests": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [_node(1, "Add", "2026-09-01T12:00:00Z", ["a.py"]),
                  _node(2, "Fix #1", "2026-09-01T13:00:00Z", ["a.py"])]}}}}
    rc = fam.main(["--json"], runner=_runner([page]),
                  now=T0 + datetime.timedelta(days=5))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["judged"] == 2 and out["floor"] == 1


def test_github_failure_exits_1_not_a_clean_rate(capsys):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "HTTP 401")
    assert fam.main([], runner=run, now=T0) == 1
    assert "CANNOT READ GITHUB" in capsys.readouterr().out


def test_zero_prs_exits_1(capsys):
    empty = {"data": {"repository": {"pullRequests": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}
    assert fam.main([], runner=_runner([empty]), now=T0) == 1
