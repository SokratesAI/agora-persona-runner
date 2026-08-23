"""Tests for the read-only staleness pass over Nova's own capture files.

The property that matters most is not what the tool flags -- it is that
`--proposal` cannot lose a sentence. Idea #83's own note names that as
the design risk ("can quietly delete a fact I needed, and I would not
know, because the evidence would be gone"), so the round-trip tests
below assert the bullet set is preserved exactly, not merely that the
counts line up.
"""

import os

import pytest

from tools import dream_pass


HEADER = "# Nova — Issues\n\n"


def doc(*bullets):
    return HEADER + "\n".join(bullets) + "\n"


def test_parse_takes_top_level_bullets_and_their_continuations():
    text = doc(
        "- 2026-08-01 (Cycle 1) — first note",
        "  continued on a second line",
        "- 2026-08-02 (Cycle 2) — second note",
    )
    bullets = dream_pass.parse(text)
    assert len(bullets) == 2
    assert "continued on a second line" in bullets[0].text
    assert bullets[1].cycle == 2
    assert bullets[1].date == "2026-08-02"


def test_parse_ignores_headings_and_prose():
    text = "# Nova — Issues\n\nCrude capture only.\n\n- a real bullet\n"
    assert len(dream_pass.parse(text)) == 1


def test_done_marker_is_read_from_the_head_line_only():
    bullets = dream_pass.parse(doc(
        "- DONE (Cycle 9): fixed in runner#1 — 2026-08-01 (Cycle 5) — the thing",
        "- 2026-08-02 (Cycle 6) — a note that mentions DONE in passing",
    ))
    assert [b.done for b in bullets] == [True, False]


def test_propose_moves_done_bullets_and_keeps_every_other_word(tmp_path):
    before = doc(
        "- DONE (Cycle 9): fixed — 2026-08-01 (Cycle 5) — the first thing",
        "- 2026-08-02 (Cycle 6) — a live note",
        "- DONE (Cycle 10): also fixed — 2026-08-03 (Cycle 7) — the second thing",
    )
    after, moved = dream_pass.propose(before)

    assert len(moved) == 2
    assert dream_pass.check(before, after, moved) is None
    assert "## Retired" in after
    # Order within the file changed; the set of bullets did not.
    assert {b.text for b in dream_pass.parse(before)} == \
           {b.text for b in dream_pass.parse(after)}
    # The live note stays above the retired section.
    live, retired = after.split("## Retired", 1)
    assert "a live note" in live
    assert "a live note" not in retired
    assert "the second thing" in retired


def test_propose_is_a_no_op_when_nothing_is_done():
    before = doc("- 2026-08-02 (Cycle 6) — a live note")
    after, moved = dream_pass.propose(before)
    assert moved == []
    assert after == before


def test_propose_is_idempotent():
    before = doc(
        "- DONE (Cycle 9): fixed — the first thing",
        "- 2026-08-02 (Cycle 6) — a live note",
    )
    once, _ = dream_pass.propose(before)
    twice, moved = dream_pass.propose(once)
    assert moved == []
    assert twice == once


def test_check_catches_a_bullet_that_went_missing():
    before = doc("- DONE (Cycle 9): fixed — the first thing",
                 "- 2026-08-02 (Cycle 6) — a live note")
    after, moved = dream_pass.propose(before)
    mangled = after.replace("- 2026-08-02 (Cycle 6) — a live note\n", "")
    assert "vanished" in dream_pass.check(before, mangled, moved)


def test_paths_only_come_from_backticked_file_shaped_spans():
    bullets = dream_pass.parse(doc(
        "- a note about `tools/thing.py` and `agora_runner/other.js`",
        "- a note about the poll loop and his ideas.md and `cli.py`",
        "- a note about `projects/sokrates/projects/nova/issues.md`",
    ))
    assert bullets[0].paths() == ["tools/thing.py", "agora_runner/other.js"]
    # No directory, and no backticks: neither is checkable.
    assert bullets[1].paths() == []
    # A vault document is not in any checkout and never could be.
    assert bullets[2].paths() == []


def test_dead_paths_needs_a_checkout_to_answer(tmp_path):
    bullets = dream_pass.parse(doc("- a note about `tools/gone.py`"))
    assert dead_names(dream_pass.dead_paths(bullets, [])) == []
    assert dead_names(dream_pass.dead_paths(bullets, [str(tmp_path)])) == \
        [["tools/gone.py"]]


def dead_names(flagged):
    return [missing for _bullet, missing in flagged]


def test_a_live_path_is_not_flagged(tmp_path):
    os.makedirs(tmp_path / "tools")
    (tmp_path / "tools" / "here.py").write_text("")
    bullets = dream_pass.parse(doc("- a note about `tools/here.py`"))
    assert dream_pass.dead_paths(bullets, [str(tmp_path)]) == []


def test_a_path_prefixed_with_its_own_repo_name_resolves(tmp_path):
    repo = tmp_path / "platform-config"
    os.makedirs(repo / "deployments")
    (repo / "deployments" / "app.yaml").write_text("")
    bullets = dream_pass.parse(doc(
        "- a note about `platform-config/deployments/app.yaml`"))
    assert dream_pass.dead_paths(bullets, [str(repo)]) == []


def test_the_repo_name_strip_does_not_match_a_different_repo(tmp_path):
    repo = tmp_path / "platform-config"
    os.makedirs(repo / "deployments")
    (repo / "deployments" / "app.yaml").write_text("")
    bullets = dream_pass.parse(doc(
        "- a note about `agora/deployments/app.yaml`"))
    assert dead_names(dream_pass.dead_paths(bullets, [str(repo)])) == \
        [["agora/deployments/app.yaml"]]


def test_duplicates_cluster_two_reports_of_the_same_bug():
    bullets = dream_pass.parse(doc(
        "- 2026-08-16 (Cycle 227) — `vault_tool.py recent 12` returned 31 rows;"
        " the count argument does not bound the result",
        "- 2026-08-13 (Cycle 170) — `vault_tool.py recent 12` returned 35 rows;"
        " the count argument does not bound the result",
        "- 2026-08-14 (Cycle 200) — the newspaper generator walks categories in"
        " config order and dies in the same place",
    ))
    clusters = dream_pass.duplicates(bullets, 0.5)
    assert len(clusters) == 1
    assert {b.index for b in clusters[0]} == {0, 1}


def test_duplicates_do_not_fire_on_shared_boilerplate():
    """The stopword list is load-bearing, so break it here and not by eye.

    These two bullets are about different things and share almost every
    grammatical word. Without `_STOP` their overlap is 0.71 and the
    clusterer joins them; with it the overlap is 0.43 and it does not.
    The first version of this test used two ordinary sentences and
    passed with the stopword list deleted -- it pinned nothing, and the
    mutation check is what said so.
    """
    bullets = dream_pass.parse(doc(
        "- it is not the case that this is a bug in the form and it does not"
        " have a test at all",
        "- it is not the case that this is a bug in the cache and it does not"
        " have a log at all",
    ))
    assert dream_pass.duplicates(bullets, 0.5) == []


def test_main_refuses_to_write_the_proposal_over_the_source(tmp_path, capsys):
    path = tmp_path / "issues.md"
    path.write_text(doc("- DONE (Cycle 9): fixed — a thing"))
    rc = dream_pass.main(["--file", str(path), "--proposal", str(path)])
    assert rc == 1
    assert "never edits in place" in capsys.readouterr().err
    # And the source really is untouched.
    assert "## Retired" not in path.read_text()


def test_main_refuses_a_checkout_that_is_not_there(tmp_path, capsys):
    path = tmp_path / "issues.md"
    path.write_text(doc("- a note"))
    assert dream_pass.main(["--file", str(path), "--repo", str(tmp_path / "nope")]) == 1
    assert "no such checkout" in capsys.readouterr().err


def test_main_reports_and_writes_a_proposal(tmp_path, capsys):
    src = tmp_path / "issues.md"
    out = tmp_path / "proposed.md"
    src.write_text(doc(
        "- DONE (Cycle 9): fixed — a finished thing",
        "- 2026-08-02 (Cycle 6) — a live note about `tools/gone.py`",
    ))
    rc = dream_pass.main(["--file", str(src), "--repo", str(tmp_path),
                          "--proposal", str(out)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "2 bullets, 1 marked DONE" in printed
    assert "tools/gone.py" in printed
    assert src.read_text() == doc(
        "- DONE (Cycle 9): fixed — a finished thing",
        "- 2026-08-02 (Cycle 6) — a live note about `tools/gone.py`",
    )
    assert "## Retired" in out.read_text()


@pytest.mark.parametrize("threshold", [0.0, 1.0])
def test_duplicate_threshold_extremes_do_not_crash(threshold):
    bullets = dream_pass.parse(doc("- one note", "- another note"))
    dream_pass.duplicates(bullets, threshold)
