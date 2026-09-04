"""The twelve-hour recap card: parsing, freshness, and the count on screen."""

import datetime

import pytest

from agora_runner.nova_recap import (
    RECAP_PATH, STALE_AFTER_HOURS, parse_recap, recap_page,
)


OSLO = datetime.timezone(datetime.timedelta(hours=2))

DOC = """---
type: log
tags: [agora, recap]
---

# Last 12 hours

<!-- generated: 2026-09-04T11:00+02:00 | cycles 871-901 -->

- **Telegram works both ways.** You can write back to the bot now.
- **The start page exists.** Every service has a card.
- A bullet with no bold lead.
"""


def _at(hour, minute=0):
    return datetime.datetime(2026, 9, 4, hour, minute, tzinfo=OSLO)


def test_bullets_split_into_lead_and_rest():
    payload = parse_recap(DOC, now=_at(12))
    assert [b["lead"] for b in payload["bullets"]] == [
        "Telegram works both ways.", "The start page exists.", "",
    ]
    assert payload["bullets"][0]["text"] == "You can write back to the bot now."
    assert payload["bullets"][2]["text"] == "A bullet with no bold lead."


def test_the_stamp_is_read_and_shown_in_oslo_time():
    payload = parse_recap(DOC, now=_at(12))
    assert payload["writtenLabel"] == "11:00"
    assert payload["cycles"] == "871-901"
    assert payload["ageHours"] == 1.0
    assert payload["stale"] is False


def test_an_old_recap_is_stale():
    payload = parse_recap(DOC, now=_at(11 + int(STALE_AFTER_HOURS), 1))
    assert payload["ageHours"] > STALE_AFTER_HOURS
    assert payload["stale"] is True


def test_a_recap_with_no_readable_stamp_reads_as_stale():
    """The one case the reader cannot judge for himself, so the card says so.

    A missing stamp used to be the comfortable default -- no age, no
    warning, a card that presents itself as current. That is a positive
    result guaranteed in advance: an unparseable timestamp and a fresh
    one would render identically.
    """
    payload = parse_recap("# Last 12 hours\n\n- One bullet.\n", now=_at(12))
    assert payload["ageHours"] is None
    assert payload["stale"] is True
    assert payload["writtenLabel"] == ""


def test_a_naive_stamp_is_not_trusted():
    """No offset means no answer. Guessing UTC here would be off by two
    hours in the direction that makes a stale card look fresh."""
    doc = DOC.replace("2026-09-04T11:00+02:00", "2026-09-04T11:00")
    payload = parse_recap(doc, now=_at(12))
    assert payload["ageHours"] is None
    assert payload["stale"] is True


def test_an_empty_vault_file_is_an_empty_card_not_a_crash():
    payload = parse_recap("", now=_at(12))
    assert payload["bullets"] == []
    assert payload["stale"] is True


def test_frontmatter_bullets_are_not_read_as_recap_bullets():
    """`tags: [agora, recap]` is not a bullet, and neither is a `- ` line
    inside the frontmatter block. Stripping the block is what stops the
    card printing the file's own metadata at him."""
    doc = "---\ntype: log\n- not a bullet\n---\n\n- **Real.** Yes.\n"
    payload = parse_recap(doc, now=_at(12))
    assert [b["lead"] for b in payload["bullets"]] == ["Real."]


def test_the_page_counts_server_side():
    page = recap_page(parse_recap(DOC, now=_at(12)))
    assert page["total"] == 3


def test_the_path_is_under_nova_resources():
    assert RECAP_PATH.endswith("/nova/resources/recap.md")
