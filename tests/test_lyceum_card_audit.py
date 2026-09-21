"""The audit has to fail on a card that is wrong, or a clean sweep says nothing.

Every test here builds the documents by hand rather than reading CouchDB --
the point of the module is what it concludes from a pair of documents, and
that question has an answer without a database.
"""

import pytest

from tools import lyceum_card_audit as audit_mod


def claim(claim_id="claim:c:ch:000", text="Descriptive analytics is the easiest form to implement.",
          grade="high"):
    return {"_id": claim_id, "type": "claim", "text": text, "grade": grade}


def card(card_id="card:c:ch:000", claim_id="claim:c:ch:000", card_type="true_false",
         grade="high", evidence="the easiest form to implement", **rest):
    doc = {"_id": card_id, "type": "card", "cardType": card_type, "claimId": claim_id,
           "grade": grade, "evidence": evidence}
    doc.update(rest)
    return doc


def failures_for(cards, claims):
    return [why for _, why in audit_mod.audit(cards, claims)[0]]


def test_a_card_that_matches_its_claim_passes():
    assert failures_for([card()], [claim()]) == []


def test_a_card_that_upgrades_its_own_grade_fails():
    """The one that matters: step 7 exists so an untested assertion is not
    practised as a settled fact, and a card is the only thing a reader sees."""
    assert failures_for([card(grade="high")], [claim(grade="ungrounded")]) == [
        "grade 'high' but its claim is 'ungrounded'"
    ]


def test_a_card_naming_a_claim_that_is_not_there_fails():
    assert failures_for([card(claim_id="claim:c:ch:999")], [claim()]) == [
        "names a claim that is not in the database"
    ]


def test_evidence_the_claim_never_said_fails():
    assert failures_for([card(evidence="a span the claim never contained")], [claim()]) == [
        "evidence span is not in the claim it came from"
    ]


def test_a_card_with_no_evidence_fails():
    assert failures_for([card(evidence="")], [claim()]) == ["carries no evidence span"]


def test_markdown_and_case_are_not_findings():
    """The extractor keeps the chapter's bold and a model lowercases a leading
    capital. Reporting those would be a checker nobody reads."""
    cards = [card(evidence="bidirectional causation", card_type="written")]
    claims = [claim(text="**Bidirectional causation.** The two variables influence each other.")]
    assert failures_for(cards, claims) == []


def test_a_cloze_with_no_blank_fails():
    cards = [card(card_type="cloze", prompt="The easiest form is the answer.",
                  answer="easiest form to implement")]
    assert "cloze prompt has no blank to fill" in failures_for(cards, [claim()])


def test_a_cloze_answer_the_claim_never_said_fails():
    cards = [card(card_type="cloze", prompt="The ___ is easiest.",
                  answer="predictive modelling")]
    assert "cloze answer is not in the claim it came from" in failures_for(cards, [claim()])


def test_a_good_cloze_passes():
    cards = [card(card_type="cloze", prompt="Descriptive analytics is ___.",
                  answer="the easiest form to implement")]
    assert failures_for(cards, [claim()]) == []


def test_a_multiple_choice_answer_outside_its_options_fails():
    cards = [card(card_type="multiple_choice", answer="predictive",
                  options=["descriptive", "diagnostic"])]
    assert "answer is not among its own options" in failures_for(cards, [claim()])


def test_a_good_multiple_choice_passes():
    cards = [card(card_type="multiple_choice", answer="descriptive",
                  options=["descriptive", "diagnostic"])]
    assert failures_for(cards, [claim()]) == []


def test_the_control_breaks_every_invariant_and_writes_nothing_back():
    """`--control` is what makes a clean run evidence. If it ever stops
    producing all five kinds of failure, a clean sweep is unverified again."""
    cards = [
        card("card:c:ch:000", card_type="written"),
        card("card:c:ch:001", card_type="written"),
        card("card:c:ch:002", card_type="multiple_choice", answer="descriptive",
             options=["descriptive", "diagnostic"]),
        card("card:c:ch:003", card_type="cloze", prompt="Descriptive analytics is ___.",
             answer="the easiest form to implement"),
    ]
    claims = [claim()]
    original = [dict(c) for c in cards]

    assert failures_for(cards, claims) == []

    broken = audit_mod.break_one_of_each(cards)
    reasons = failures_for(broken, claims)

    assert cards == original, "the control must not touch the caller's documents"
    for expected in ("evidence span is not in the claim it came from",
                     "names a claim that is not in the database",
                     "answer is not among its own options",
                     "cloze prompt has no blank to fill",
                     "cloze answer is not in the claim it came from"):
        assert expected in reasons, expected
    assert any(reason.startswith("grade ") for reason in reasons)


def test_main_refuses_with_no_course_named():
    with pytest.raises(SystemExit):
        audit_mod.main([])
