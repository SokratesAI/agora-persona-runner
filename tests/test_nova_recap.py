"""The twelve-hour recap card: parsing, freshness, and the count on screen."""

import datetime

import pytest

from agora_runner import nova_recap
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


def test_a_markdown_link_becomes_a_part_the_page_can_draw():
    """His capture 2026-09-04 12:29: the card named the tailnet start page
    and gave him no way to open it. The split happens here so the page is
    a loop with one `if` in it and never parses markdown."""
    payload = parse_recap(
        "- **Hub.** Your start page is at [hub](https://hub.tailc83eb3.ts.net/) now.\n",
        now=_at(12),
    )
    bullet = payload["bullets"][0]
    assert bullet["parts"] == [
        {"text": "Your start page is at ", "href": ""},
        {"text": "hub", "href": "https://hub.tailc83eb3.ts.net/"},
        {"text": " now.", "href": ""},
    ]
    # The plain reading of the same sentence keeps the label and drops the
    # markup -- anything that wants one string gets a readable one.
    assert bullet["text"] == "Your start page is at hub now."


def test_a_bare_url_is_linked_and_keeps_the_full_stop_out_of_it():
    """A URL at the end of a sentence takes the period with it otherwise,
    and the link then 404s on a character he cannot see."""
    payload = parse_recap("- Open https://hub.tailc83eb3.ts.net/.\n", now=_at(12))
    assert payload["bullets"][0]["parts"] == [
        {"text": "Open ", "href": ""},
        {"text": "https://hub.tailc83eb3.ts.net/", "href": "https://hub.tailc83eb3.ts.net/"},
        {"text": ".", "href": ""},
    ]


def test_a_link_in_the_lead_is_split_too():
    payload = parse_recap("- **[Galaxy](/galaxy)** is live.\n", now=_at(12))
    bullet = payload["bullets"][0]
    assert bullet["leadParts"] == [{"text": "Galaxy", "href": "/galaxy"}]
    assert bullet["lead"] == "[Galaxy](/galaxy)"
    assert bullet["parts"] == [{"text": "is live.", "href": ""}]


def test_a_bullet_with_no_link_has_one_plain_part():
    """The negative half. Without this the link tests would pass against a
    parser that wrapped every bullet in an anchor."""
    payload = parse_recap("- Nothing to open here.\n", now=_at(12))
    assert payload["bullets"][0]["parts"] == [
        {"text": "Nothing to open here.", "href": ""},
    ]
    assert all(not part["href"] for part in payload["bullets"][0]["parts"])


# --- the journal marker in the stamp (his issue #219) ---

def test_the_stamp_carries_the_entry_the_card_was_built_from():
    payload = nova_recap.parse_recap(
        nova_recap.render(["**A thing** happened"], cycles="1500-1508",
                          journal="1571-cycle-1508.md"))
    assert payload["journal"] == "1571-cycle-1508.md"
    assert payload["cycles"] == "1500-1508"


def test_cycles_still_parse_when_a_journal_marker_follows_them():
    # The `cycles` group used to be `[^>]*?`, which would have swallowed
    # ` | journal ...` whole. This is the test that separates the two.
    payload = nova_recap.parse_recap(
        "<!-- generated: 2026-09-13T13:00+02:00 | cycles 1500-1508 | "
        "journal 1571-cycle-1508.md -->\n\n- a bullet\n")
    assert payload["cycles"] == "1500-1508"


def test_a_card_written_before_the_marker_existed_still_parses():
    payload = nova_recap.parse_recap(
        "<!-- generated: 2026-09-13T13:00+02:00 | cycles 900-901 -->\n\n- a bullet\n")
    assert payload["journal"] == ""
    assert payload["cycles"] == "900-901"
    assert len(payload["bullets"]) == 1


def test_render_omits_the_marker_rather_than_writing_an_empty_one():
    # `| journal ` with nothing after it would parse back as "" anyway, but
    # it is a lie on the face of the document.
    stamp = [l for l in nova_recap.render(["a bullet"]).splitlines()
             if l.startswith("<!-- generated:")]
    assert stamp and "journal" not in stamp[0]
    assert nova_recap.stamp_journal("") == ""
    assert nova_recap.stamp_journal("  x.md ") == " | journal x.md"


def test_an_unknown_field_is_ignored_rather_than_losing_the_whole_stamp():
    # The failure this guards is not cosmetic: an unmatched stamp gives back
    # `written: ""`, and `parse_recap` reads an unknown age as stale on
    # purpose -- so one field a future writer adds would take the card's date
    # off his screen. Ignoring the field keeps everything else true.
    payload = nova_recap.parse_recap(
        "<!-- generated: 2026-09-13T13:00+02:00 | cycles 1500-1508 | "
        "journal 1571-cycle-1508.md | built-by haiku -->\n\n- a bullet\n")
    assert payload["cycles"] == "1500-1508"
    assert payload["journal"] == "1571-cycle-1508.md"
    assert payload["written"] == "2026-09-13T13:00+02:00"
