"""The rule: what counts as "I have already researched this".

Pinned against the pair that made this exist -- Cycle 667 and Cycle 669 both
surveyed what else to run on the NAS, two cycles apart, on the same capture.
"""

import pytest

from agora_runner.research_topics import (
    STRONG, index, is_strong, related, render, slug_of, words,
)

FOLDER = "projects/sokrates/projects/agora/nova/resources/research/"
REAL = [FOLDER + n for n in (
    "nas-linuxserver-survey.md",
    "nas-linuxserver-survey-2026-08-30.md",
    "nas-k3s-2026-08-29.md",
    "platform-scan-2026-09-05.md",
    "platform-scan-2026-09-08.md",
    "concurrent-cycles-duplicate-work-2026-09-14.md",
    "config-repo-anti-pattern-audit-2026-09-03.md",
)]

NAS_CAPTURE = ("Go through the linuxserver repositories and see what else is "
               "worth adding to the NAS")


def test_the_duplicate_survey_is_caught():
    rows = related(NAS_CAPTURE, REAL)
    strong = [slug_of(p) for r in rows if is_strong(r) for p in [r[2]]]
    assert strong == ["nas-linuxserver-survey-2026-08-30", "nas-linuxserver-survey"]


def test_a_single_shared_word_is_not_a_duplicate():
    """`nas-k3s` shares only "nas" and scores 100% coverage on it.

    Coverage alone would call that a duplicate and send a cycle off to read
    a k3s feasibility note about a software survey.
    """
    rows = {slug_of(r[2]): r for r in related(NAS_CAPTURE, REAL)}
    assert rows["nas-k3s-2026-08-29"][0] == pytest.approx(1.0)
    assert not is_strong(rows["nas-k3s-2026-08-29"])


def test_duplicates_sort_above_a_higher_scoring_single_word():
    rows = related(NAS_CAPTURE, REAL)
    assert slug_of(rows[0][2]).startswith("nas-linuxserver-survey")


def test_a_date_is_not_a_subject():
    """Two unrelated notes written the same week must not match on the date."""
    assert words("platform-scan-2026-09-05") == ["platform", "scan"]
    assert related("2026-09-05", REAL) == []


def test_stopwords_alone_match_nothing():
    assert related("what else is worth doing about the", REAL) == []


def test_an_empty_topic_returns_nothing_rather_than_everything():
    assert related("", REAL) == []
    assert related(None, REAL) == []


def test_long_topic_cannot_dilute_its_way_under_the_threshold():
    """Score is coverage of the *document*, so padding the topic changes nothing."""
    padded = NAS_CAPTURE + " " + " ".join(f"word{n}" for n in range(200))
    assert [r[2] for r in related(padded, REAL) if is_strong(r)] == \
           [r[2] for r in related(NAS_CAPTURE, REAL) if is_strong(r)]


def test_exact_topic_scores_full_coverage():
    rows = related("concurrent cycles duplicate work", REAL)
    assert rows[0][0] == pytest.approx(1.0)
    assert is_strong(rows[0])
    assert STRONG <= 1.0


def test_render_says_already_researched_only_on_a_strong_match():
    assert "ALREADY RESEARCHED" in render(NAS_CAPTURE, related(NAS_CAPTURE, REAL), 83)
    weak = related("k3s on the nas", [FOLDER + "nas-auth-2026-08-29.md"])
    assert "ALREADY RESEARCHED" not in render("k3s on the nas", weak, 83)
    assert "worth one look" in render("k3s on the nas", weak, 83)


def test_render_with_no_rows_says_nothing_written_yet():
    out = render("quantum jam", [], 83)
    assert "Nothing written yet" in out and "83" in out


def test_index_lists_every_slug():
    out = index(REAL)
    assert len(out.splitlines()) == len(REAL)
    assert "nas-k3s-2026-08-29" in out
    assert ".md" not in out


def test_two_shared_words_are_not_enough_on_their_own():
    """The threshold is half the document's subject, and it has to bite.

    `config-repo-anti-pattern-audit` carries five subject words, so a topic
    sharing two of them covers 40% of it -- related, not the same question.
    Without a real threshold every two-word brush-past reads as
    ALREADY RESEARCHED, and a cycle stops researching something nobody has.
    """
    rows = related("config audit", REAL)
    hit = next(r for r in rows if "config-repo" in r[2])
    assert hit[0] == pytest.approx(0.4)
    assert len(hit[1]) == 2
    assert not is_strong(hit)
