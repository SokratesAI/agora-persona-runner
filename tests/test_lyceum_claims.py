"""Build step 7: claim atoms and GRADE marking.

The tests that matter here are the negative controls. Extraction is easy to
prove and easy to fool; what this step is actually for is refusing a citation
the model invented, so most of what follows checks that a wrong answer is
caught rather than that a right one is kept.
"""

import pytest

from tools import lyceum_claims as lc


CHAPTER = """---
title: Cohorts
---

# Cohort analysis

A cohort is a group of users who share a starting event in the same period.
Retention is measured against that starting event rather than the calendar [source: amplitude-guide.md].

Weekly cohorts smooth out weekday effects. They are the default for consumer products [source: amplitude-guide.md].

There are three kinds:

```python
# not prose, and not a claim
cohort = df.groupby("week")
```

Nobody cites this paragraph at all, so it carries no source of its own.
"""


def test_atoms_read_the_page_not_the_frontmatter():
    found = lc.atoms(CHAPTER, "analytics")
    texts = [t for t, _ in found]
    assert any(t.startswith("A cohort is a group") for t in texts)
    # Frontmatter, the heading, the fenced code and the lead-in line all go.
    assert not any("title: Cohorts" in t for t in texts)
    assert not any(t.startswith("# ") for t in texts)
    assert not any("groupby" in t for t in texts)
    assert not any(t == "There are three kinds:" for t in texts)


def test_citation_scope_is_the_paragraph_and_never_the_whole_topic():
    found = dict((t, s) for t, s in lc.atoms(CHAPTER, "analytics"))
    cohort = next(s for t, s in found.items() if t.startswith("A cohort is a group"))
    uncited = next(s for t, s in found.items() if t.startswith("Nobody cites"))
    # The marker sits on the *second* sentence of that paragraph; the first
    # one inherits it, because the paragraph is the scope.
    assert cohort == ["source:analytics:amplitude-guide"]
    # And a paragraph citing nothing gets nothing. This is the one that
    # would break if the page's `sources:` frontmatter were ever a fallback.
    assert uncited == []


def test_marker_is_stripped_from_the_claim_text():
    found = lc.atoms(CHAPTER, "analytics")
    assert not any("[source:" in t for t, _ in found)


SOURCE = {
    "_id": "source:analytics:amplitude-guide",
    "slug": "amplitude-guide",
    "title": "Amplitude's guide to retention",
    "url": "https://amplitude.com/guide",
    "retrieved": "2026-09-01",
    "body": "Retention is measured against the starting event rather than the calendar month.\nWeekly cohorts are the usual default.",
}

VAULT_NOTE = dict(SOURCE, _id="source:analytics:my-notes", url=None, retrieved=None)


def test_a_quote_that_is_not_in_the_source_is_refused():
    assert not lc.quote_is_real("Retention doubles after the third week of use", SOURCE["body"])


def test_a_real_quote_survives_reflowing():
    assert lc.quote_is_real("Retention is measured against   the starting\nevent rather than the calendar month.",
                            SOURCE["body"])


def test_a_quote_too_short_to_have_been_read_is_refused():
    # "the calendar" is genuinely in the body; it is not evidence of anything.
    assert "the calendar" in SOURCE["body"]
    assert not lc.quote_is_real("the calendar", SOURCE["body"])


def test_supported_with_a_verified_quote_grades_high_on_a_fetched_source():
    assert lc.grade("supported", True, SOURCE) == ("grounded", "high")


def test_supported_off_a_vault_note_is_moderate_not_high():
    assert lc.grade("supported", True, VAULT_NOTE) == ("grounded", "moderate")


def test_supported_with_an_unverifiable_quote_is_not_checked():
    # The whole point: the model said yes and could not show where. That is
    # not a grounded claim and it is not a checked one either.
    assert lc.grade("supported", False, SOURCE) == ("unverified", "low")


def test_not_found_is_ungrounded_and_contradicted_keeps_its_own_state():
    assert lc.grade("not_found", False, SOURCE) == ("ungrounded", "ungrounded")
    assert lc.grade("contradicted", True, SOURCE)[0] == "contradicted"


def test_parse_verdicts_ignores_prose_and_malformed_lines():
    text = (
        'Here are my answers:\n'
        '{"n": 0, "verdict": "supported", "quote": "something"}\n'
        '{"n": 1, "verdict": "maybe", "quote": ""}\n'
        '{"n": 2, "verdict"\n'
        '```\n'
        '{"n": 3, "verdict": "not_found", "quote": ""}\n'
    )
    assert lc.parse_verdicts(text) == {0: ("supported", "something"), 3: ("not_found", "")}


def _chapter_doc():
    return {"_id": "chapter:analytics:cohorts", "courseId": "course:analytics",
            "slug": "cohorts", "body": CHAPTER}


def test_a_hallucinated_citation_does_not_become_a_grounded_claim():
    """The end-to-end negative control, through the same path a real run takes."""
    def ask(prompt, model):
        return '\n'.join(f'{{"n": {n}, "verdict": "supported", "quote": "Retention doubles every week, as the data shows."}}'
                         for n in range(10))

    docs = lc.claims_for_chapter(_chapter_doc(), {SOURCE["_id"]: SOURCE},
                                 "m", ask=ask, out=lambda _: None)
    judged = [d for d in docs if d["checkedAgainst"]]
    assert judged, "the fixture must actually reach the judging path"
    assert all(d["status"] == "unverified" and d["grade"] == "low" for d in judged)
    assert all(d["quote"] is None for d in judged)


def test_a_real_quote_grounds_the_claim():
    """The positive control, so the test above is not green by refusing everything."""
    quote = "Retention is measured against the starting event rather than the calendar month."

    def ask(prompt, model):
        assert quote in prompt, "the source body must be put in front of the model"
        return '\n'.join(f'{{"n": {n}, "verdict": "supported", "quote": "{quote}"}}' for n in range(10))

    docs = lc.claims_for_chapter(_chapter_doc(), {SOURCE["_id"]: SOURCE},
                                 "m", ask=ask, out=lambda _: None)
    judged = [d for d in docs if d["checkedAgainst"]]
    assert judged and all(d["status"] == "grounded" and d["grade"] == "high" for d in judged)


def test_an_uncited_atom_is_ungrounded_and_never_judged():
    def ask(prompt, model):
        return '{"n": 0, "verdict": "supported", "quote": "Retention is measured against the starting event rather than the calendar month."}'

    docs = lc.claims_for_chapter(_chapter_doc(), {SOURCE["_id"]: SOURCE},
                                 "m", ask=ask, out=lambda _: None)
    orphan = next(d for d in docs if d["text"].startswith("Nobody cites"))
    assert orphan["status"] == "ungrounded" and orphan["sourceIds"] == []
    assert orphan["checkedAgainst"] is None


def test_the_model_is_never_asked_where_a_claim_came_from():
    """The prompt hands over text to judge; it must not ask for a source."""
    prompt = lc.build_prompt(SOURCE, [(0, "A cohort is a group of users.")])
    assert SOURCE["body"] in prompt
    lowered = prompt.lower()
    assert "do not use anything you know from elsewhere" in lowered
    assert "which source" not in lowered and "where does" not in lowered


def test_claim_ids_are_stable_across_runs():
    first = lc.claims_for_chapter(_chapter_doc(), {}, "m", ask=None, out=lambda _: None)
    second = lc.claims_for_chapter(_chapter_doc(), {}, "m", ask=None, out=lambda _: None)
    assert [d["_id"] for d in first] == [d["_id"] for d in second]
    assert first[0]["_id"] == "claim:analytics:cohorts:000"


def test_distribution_counts_levels_not_percentages():
    docs = [{"grade": "high"}, {"grade": "high"}, {"grade": "ungrounded"}]
    assert lc.distribution(docs) == {"high": 2, "ungrounded": 1}
