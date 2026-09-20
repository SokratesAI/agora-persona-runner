"""Build step 8: practice cards generated from claim atoms.

Same emphasis as step 7's tests. Writing a question is easy and easy to
fake; what this step is for is refusing a card that carries more confidence
than the claim under it, so most of what follows checks that a bad card is
thrown away rather than that a good one is kept.
"""

import json

from tools import lyceum_cards as lcards


CLAIM_TEXT = "A cohort is a group of users who share a starting event in the same calendar period."


def claim(status="grounded", grade="high", text=CLAIM_TEXT, index=0):
    return {
        "_id": f"claim:analytics:cohorts:{index:03d}",
        "type": "claim",
        "courseId": "course:analytics",
        "chapterId": "chapter:analytics:cohorts",
        "text": text,
        "status": status,
        "grade": grade,
        "sourceIds": ["source:analytics:amplitude-guide"],
        "quote": "cohorts share a starting event",
    }


def fact_row(**over):
    row = {
        "n": 0,
        "type": "true_false",
        "prompt": "A cohort is defined by a shared starting event.",
        "answer": "true",
        "options": [],
        "why": "The source defines a cohort by its starting event.",
        "evidence": "a group of users who share a starting event",
    }
    row.update(over)
    return row


# ------------------------------------------------- what becomes what


def test_status_decides_the_kind_of_card():
    fact, design, skipped = lcards.partition([
        claim(status="grounded", index=0),
        claim(status="ungrounded", grade="ungrounded", index=1),
        claim(status="contradicted", index=2),
        claim(status="unverified", grade="low", index=3),
    ])
    assert [c["_id"][-3:] for c in fact] == ["000"]
    assert [c["_id"][-3:] for c in design] == ["001"]
    # Contradicted and unverified are practised as nothing at all.
    assert sorted(c["_id"][-3:] for c in skipped) == ["002", "003"]


def test_grade_is_copied_off_the_claim_not_regenerated():
    card = lcards.make_card(claim(grade="moderate"), fact_row(), "true_false")
    assert card["grade"] == "moderate"
    assert card["claimStatus"] == "grounded"
    assert card["claimId"] == "claim:analytics:cohorts:000"
    assert card["_id"] == "card:analytics:cohorts:000"
    # Nothing the model returned can set the grade.
    loud = lcards.make_card(claim(grade="moderate"), fact_row(grade="high"), "true_false")
    assert loud["grade"] == "moderate"


# ---------------------------------------- the hallucination control


def test_evidence_must_be_a_real_span_of_the_claim():
    assert lcards.evidence_is_real("a group of users who share a starting event", CLAIM_TEXT)
    # Whitespace and case are allowed to differ; the words are not.
    assert lcards.evidence_is_real("A  GROUP of users\nwho share a starting event", CLAIM_TEXT)
    # A plausible sentence that is simply not in the claim.
    assert not lcards.evidence_is_real("a group of customers who share a purchase event", CLAIM_TEXT)


def test_a_few_words_are_not_evidence():
    # Short enough to be a coincidence rather than a span that was read.
    assert not lcards.evidence_is_real("a cohort is", CLAIM_TEXT)


def test_invented_evidence_refuses_the_card():
    assert lcards.check_fact(fact_row(), CLAIM_TEXT) is None
    bad = lcards.check_fact(fact_row(evidence="users are grouped by their acquisition channel"), CLAIM_TEXT)
    assert bad == "evidence not in the claim"


# ------------------------------------------- per-type structural checks


def test_cloze_answer_must_be_in_the_claim_and_the_prompt_must_have_a_blank():
    good = fact_row(type="cloze", prompt="A ___ is a group of users who share a starting event.",
                    answer="cohort")
    assert lcards.check_fact(good, CLAIM_TEXT) is None
    assert lcards.check_fact(dict(good, answer="segment"), CLAIM_TEXT) == "cloze answer not in the claim"
    assert lcards.check_fact(dict(good, prompt="What is a cohort?"), CLAIM_TEXT) == "cloze without a blank"


def test_multiple_choice_answer_must_be_one_of_the_options():
    good = fact_row(type="multiple_choice", prompt="What defines a cohort?",
                    answer="a shared starting event",
                    options=["a shared starting event", "a shared plan", "a shared region"])
    assert lcards.check_fact(good, CLAIM_TEXT) is None
    assert lcards.check_fact(dict(good, answer="a shared birthday"), CLAIM_TEXT) == \
        "answer is not one of the options"
    assert lcards.check_fact(dict(good, options=["a shared starting event", "a shared plan"]), CLAIM_TEXT) == \
        "fewer than three options"
    assert lcards.check_fact(dict(good, options=["a shared starting event", "A shared starting event",
                                                 "a shared plan"]), CLAIM_TEXT) == "duplicate options"


def test_true_false_answer_must_actually_be_true_or_false():
    assert lcards.check_fact(fact_row(answer="false"), CLAIM_TEXT) is None
    assert lcards.check_fact(fact_row(answer="mostly"), CLAIM_TEXT) == "true/false answer is neither"


def test_written_answer_must_be_a_model_answer_not_a_word():
    short = fact_row(type="written", prompt="Why measure retention from the starting event?",
                     answer="Because.")
    assert lcards.check_fact(short, CLAIM_TEXT) == "model answer too short"
    long = dict(short, answer="Because the calendar does not know when each user began, so a "
                              "shared starting event is the only common origin.")
    assert lcards.check_fact(long, CLAIM_TEXT) is None


def test_a_type_outside_the_four_is_refused():
    assert lcards.check_fact(fact_row(type="essay"), CLAIM_TEXT) == "type 'essay'"
    assert lcards.check_fact(fact_row(type=lcards.DESIGN_TYPE), CLAIM_TEXT) == "type 'design'"


def test_design_card_needs_a_real_evidence_design():
    row = {"n": 0, "prompt": "Nothing tests this. What would?",
           "answer": "Run a cohort split on new signups over eight weeks and compare retention "
                     "against the calendar-aligned grouping.",
           "why": "No source addresses it.", "evidence": "a group of users who share a starting event"}
    assert lcards.check_design(row, CLAIM_TEXT) is None
    assert lcards.check_design(dict(row, answer="Test it."), CLAIM_TEXT) == "evidence design too short"
    assert lcards.check_design(dict(row, evidence="something the model made up here"), CLAIM_TEXT) == \
        "evidence not in the claim"


# ------------------------------------------------------ end to end


def test_a_batch_keeps_the_good_card_and_refuses_the_invented_one():
    claims = [claim(index=0), claim(index=1, text="Weekly cohorts smooth out weekday effects for consumer products.")]

    def ask(prompt, model):
        assert "STATEMENTS:" in prompt
        return (
            json.dumps(fact_row(n=0)) + "\n"
            + json.dumps(fact_row(n=1, evidence="monthly cohorts are the default everywhere")) + "\n"
        )

    cards, refused = lcards.cards_for_batch(claims, "m", design=False, ask=ask)
    assert [c["claimId"] for c in cards] == ["claim:analytics:cohorts:000"]
    assert refused == [("claim:analytics:cohorts:001", "evidence not in the claim")]


def test_a_claim_the_model_skipped_is_refused_not_dropped_silently():
    claims = [claim(index=0), claim(index=1)]
    cards, refused = lcards.cards_for_batch(
        claims, "m", design=False, ask=lambda p, m: json.dumps(fact_row(n=0)))
    assert len(cards) == 1
    assert refused == [("claim:analytics:cohorts:001", "no answer")]


def test_design_batch_produces_design_cards_carrying_the_ungrounded_grade():
    claims = [claim(status="ungrounded", grade="ungrounded", index=4)]
    row = {"n": 0, "prompt": "What would test this?",
           "answer": "Measure retention for both groupings over eight weeks and compare the curves.",
           "why": "Untested, not wrong.", "evidence": "a group of users who share a starting event"}
    cards, refused = lcards.cards_for_batch(claims, "m", design=True, ask=lambda p, m: json.dumps(row))
    assert refused == []
    assert cards[0]["cardType"] == "design"
    assert cards[0]["grade"] == "ungrounded"
    # A design card never carries options, and it is not one of the fact types.
    assert cards[0]["options"] == []
    assert cards[0]["cardType"] not in lcards.FACT_TYPES


def test_options_are_dropped_for_every_type_but_multiple_choice():
    card = lcards.make_card(claim(), fact_row(options=["a", "b", "c"]), "true_false")
    assert card["options"] == []


def test_parse_rows_ignores_commentary_and_broken_lines():
    text = ("Here are the cards:\n"
            "```\n"
            + json.dumps(fact_row(n=0)) + "\n"
            "{not json at all}\n"
            + json.dumps(fact_row(n=1)) + "\n"
            "That's all.\n")
    rows = lcards.parse_rows(text)
    assert sorted(rows) == [0, 1]
