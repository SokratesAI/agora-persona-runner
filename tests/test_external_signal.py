"""`tools.external_signal` — the outside reading of this loop (idea #88).

Every check here is two-sided. A marker test that only proves the tool
catches "wrong" would still pass if the tool flagged every comment ever
written, so each one is paired with text it must *not* flag. The one that
matters most is the empty-corpus case: zero corrections is the best
possible score, and it is exactly what a broken parser produces.
"""

import io
import sys

import pytest

from tools import external_signal as es


CORPUS = """---
type: log
---

# Comments

## New

## Acknowledged

### Cycle 400 · 2026-08-25 09:00

Again, I do not want to repeat myself with this. It is wrong and it is confusing.

#### Nova · 2026-08-25 09:05

That is wrong of me and I am annoyed at myself.

### Cycle 380 · 2026-08-24 08:00

Just testing out the new notes page. Hello there.

### Cycle 237 · 2026-08-16 12:00

You often say nobody had read it. That is wrong.

### Cycle 100 · 2026-08-10 07:00

Maybe turn it on again?
"""


def test_parses_only_the_owners_headings():
    rows = es.parse_comments(CORPUS)
    assert [cycle for cycle, _, _ in rows] == [100, 237, 380, 400]
    # The `#### Nova` block sits between two of his and is full of markers.
    # If it leaked into a body, cycle 400 would carry "annoyed" as well.
    body_400 = [body for cycle, _, body in rows if cycle == 400][0]
    assert "annoyed" not in body_400
    assert "repeat myself" in body_400


def test_correction_markers_fire_and_do_not_over_fire():
    corrections, _ = es.classify("You often say nobody had read it. That is wrong.")
    assert "wrong" in corrections
    corrections, _ = es.classify("Just testing out the new notes page. Hello there.")
    assert corrections == []


def test_repetition_markers_ignore_a_bare_again():
    # His commonest use of the word, and not a repetition.
    _, repeats = es.classify("I do think the Sentinel is paused. Maybe turn it on again?")
    assert repeats == []
    _, repeats = es.classify("Again, and write this down to your personality.")
    assert repeats == ["Again,"]


def test_a_comment_counts_once_however_many_markers_it_carries():
    rows, weeks = es.measure(CORPUS)
    # Cycle 400 carries a correction marker and a repetition marker.
    # Cycle 400 carries three correction markers and one repetition marker;
    # it must still move each column by exactly one.
    assert es.classify([b for c, _, b in es.parse_comments(CORPUS) if c == 400][0])[0] \
        == ["wrong", "do not", "confusing"]
    tally = weeks[es.week_of("2026-08-25")]
    assert tally["comments"] == 2          # cycles 380 and 400
    assert tally["corrected"] == 1
    assert tally["repeated"] == 1


def test_weeks_are_iso_weeks_and_split_the_corpus():
    _, weeks = es.measure(CORPUS)
    # 08-10 is a Monday, so W33 runs to 08-16 and W35 opens on 08-24.
    assert sorted(weeks) == ["2026-W33", "2026-W35"]
    assert weeks["2026-W33"]["comments"] == 2    # 08-10 and 08-16
    assert weeks["2026-W33"]["corrected"] == 1   # 08-16, "That is wrong"


def test_empty_corpus_is_reported_as_no_instrument_not_as_a_clean_week():
    rows, weeks = es.measure("# Comments\n\nnothing here\n")
    out = io.StringIO()
    assert es.report(rows, weeks, out=out) == 1
    assert "NO CORPUS" in out.getvalue()
    assert "0%" not in out.getvalue()


def test_unreadable_corpus_exits_one_rather_than_scoring_zero(monkeypatch):
    monkeypatch.setattr(es, "_fetch", lambda path: None)
    assert es.main([]) == 1


def test_a_not_found_body_is_not_read_as_an_empty_file(monkeypatch):
    class Done:
        returncode = 0
        stdout = "[not found: projects/x.md]\n"
        stderr = ""

    monkeypatch.setattr(es.subprocess, "run", lambda *a, **k: Done())
    assert es._fetch("projects/x.md") is None


def test_part_week_is_marked_and_a_finished_week_is_not():
    assert es._week_complete("2026-08-23") is True     # a Sunday
    assert es._week_complete("2026-08-27") is False    # a Thursday
    rows, weeks = es.measure(CORPUS)
    out = io.StringIO()
    es.report(rows, weeks, out=out)
    assert "part week" in out.getvalue()


def test_show_lists_the_matched_comments_and_is_off_by_default():
    rows, weeks = es.measure(CORPUS)
    quiet, loud = io.StringIO(), io.StringIO()
    es.report(rows, weeks, show=False, out=quiet)
    es.report(rows, weeks, show=True, out=loud)
    assert "cycle 237" not in quiet.getvalue()
    assert "cycle 237" in loud.getvalue()
    # The comment carrying no marker never appears in either.
    assert "cycle 380" not in loud.getvalue()


def test_rules_print_both_lists():
    out = io.StringIO()
    es.print_rules(out=out)
    assert "wrong" in out.getvalue()
    assert "repeat myself" in out.getvalue()


@pytest.mark.parametrize("phrase", ["do not", "should not", "annoying", "hard to read"])
def test_each_grounded_marker_is_actually_wired_up(phrase):
    corrections, _ = es.classify(f"I think you {phrase} write it that way.")
    assert corrections, f"{phrase!r} is in the rules but matches nothing"


# --- the second corpus: notes.md, dated off the vault's backup mirror -------
# Two-sided the same way the rest of this file is. The one that matters is the
# unreachable-mirror case: dating nothing would drop every note from the
# trend, which reads as a week he did not correct me.

NOTES = """---
type: log
contract: Edvard writes in the bare bullet list at the top.
---

- 

## Read

- You often say nobody had read it. That is wrong.
  - Read Cycle 328. Agreed, and I have written it down.

- Just testing out the new notes page. Hello there.
  - Read Cycle 380. Hello. It arrived.
"""


def test_notes_parses_his_bullets_and_not_my_replies():
    notes = es.parse_notes(NOTES)
    assert notes == ["You often say nobody had read it. That is wrong.",
                     "Just testing out the new notes page. Hello there."]
    # The empty placeholder is the file waiting for him, not a note.
    assert "" not in notes
    # My indented reply is never a note, even though it is a bullet.
    assert not any("Read Cycle" in note for note in notes)


def test_a_note_is_dated_by_the_first_snapshot_that_holds_it():
    notes = es.parse_notes(NOTES)
    snapshots = [("aaa", "2026-08-16"), ("bbb", "2026-08-24")]
    bodies = {
        "aaa": "- You often say nobody had read it. That is wrong.\n",
        "bbb": NOTES,
    }
    dated, undated = es.date_notes(notes, snapshots, body_of=bodies.get)
    assert dated[notes[0]] == "2026-08-16"   # present in the older snapshot
    assert dated[notes[1]] == "2026-08-24"   # first seen in the newer one
    assert undated == []


def test_a_note_no_snapshot_holds_is_reported_undated_not_dated_today():
    notes = es.parse_notes(NOTES)
    dated, undated = es.date_notes(notes, [("aaa", "2026-08-16")],
                                   body_of=lambda sha: "- something else\n")
    assert dated == {}
    assert undated == notes
    out = io.StringIO()
    rows, weeks = es.measure(CORPUS, dated)
    es.report(rows, weeks, out=out, undated=undated)
    assert "matched no snapshot" in out.getvalue()


def test_an_unreachable_mirror_is_a_missing_instrument_not_a_quiet_corpus(monkeypatch):
    monkeypatch.setattr(es, "mirror_snapshots", lambda: None)
    dated, undated = es.date_notes(es.parse_notes(NOTES),
                                   body_of=lambda sha: None)
    assert dated is None
    assert undated == es.parse_notes(NOTES)
    rows, weeks = es.measure(CORPUS, None)
    out = io.StringIO()
    es.report(rows, weeks, out=out, mirror_read=False)
    assert "NOTES UNDATED" in out.getvalue()
    # And it must not print the undated-notes line instead, which would read
    # as "the mirror answered and held none of them".
    assert "matched no snapshot" not in out.getvalue()


def test_mirror_snapshots_returns_none_when_the_call_does_not_answer(monkeypatch):
    monkeypatch.setattr(es, "_gh_json", lambda *a, **k: None)
    assert es.mirror_snapshots() is None
    monkeypatch.setattr(es, "_gh_json", lambda *a, **k: [
        {"sha": "bbb", "commit": {"committer": {"date": "2026-08-24T10:00:00Z"}}},
        {"sha": "aaa", "commit": {"committer": {"date": "2026-08-16T10:00:00Z"}}},
    ])
    assert es.mirror_snapshots() == [("aaa", "2026-08-16"), ("bbb", "2026-08-24")]


def test_notes_join_the_weekly_trend_and_are_labelled_in_show():
    notes = es.parse_notes(NOTES)
    dated = {notes[0]: "2026-08-16"}
    rows, weeks = es.measure(CORPUS, dated)
    # W33 held 2 comments before the note landed in it.
    assert weeks["2026-W33"]["comments"] == 3
    assert weeks["2026-W33"]["corrected"] == 2
    loud = io.StringIO()
    es.report(rows, weeks, show=True, out=loud)
    assert "note " in loud.getvalue()
    assert "22 note(s)" not in loud.getvalue()
    assert "1 note(s)" in loud.getvalue()


def test_measuring_without_notes_is_unchanged():
    assert es.measure(CORPUS, None)[1] == es.measure(CORPUS)[1]


def test_no_notes_flag_skips_the_mirror_entirely(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("--no-notes must not touch the mirror")
    monkeypatch.setattr(es, "mirror_snapshots", refuse)
    monkeypatch.setattr(es, "_fetch", lambda path: CORPUS)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert es.main(["--no-notes"]) == 0
