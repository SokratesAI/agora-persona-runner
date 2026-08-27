"""The Friday retrospective ledger and the page that reads it.

`nova_retro` does no I/O, so every test here is a pure function against a
literal document. The expected shapes are written as literals rather than
derived from the input for the reason `test_nova_costs` gives: a
transform compared against itself survives its own mutation.

The half worth stating out loud is the validator. This ledger gets one
row a week, written by a cycle with no memory of the previous one, and
the cost of a wrong column is not noticed until the following Friday --
so the tests that matter most here are the ones asserting that a wrong
row is *refused*, not the ones asserting a right row is accepted.
"""

import json

import pytest

from agora_runner import nova_retro, nova_sources
from tools import append_retro
from agora_runner.nova_retro import (
    RetroError,
    SCORE_KEYS,
    WEEK_KEYS,
    append,
    load,
    retros_payload,
    validate_row,
)


def row(**overrides):
    base = {
        "date": "2026-08-14",
        "cycle": 181,
        "scores": {"going": 7, "effectiveness": 6, "feeling": 8},
        "overall": "Steady, and finally measurable.",
        "good": "The loop ships something most cycles.",
        "bad": "It re-derives the same facts every time it wakes.",
        "changes": ["Write the pace number into the entry when it changed the pick."],
        "week": {
            "shipped": "The board can be filtered by project, and the app says what you missed.",
            "broke": "The journal endpoint handed out four megabytes and the pod was killed for it.",
            "stuck": "The outage alarm still waits on a GitHub notification nobody has confirmed.",
            "change": "Ask whether an instrument has ever returned a value before believing it.",
        },
    }
    base.update(overrides)
    return base


LEDGER = {"retros": [row(date="2026-08-07", cycle=120, scores={"going": 5, "effectiveness": 5, "feeling": 6}), row()]}


# --- the shape ---------------------------------------------------------


def test_the_three_scores_are_the_three_things_he_asked_to_be_rated():
    """Pinned as a literal, and this is the assertion the whole file is
    built around.

    The owner named three ratings -- how it is going, how effective, the
    overall feeling -- and the last is the one he called the most
    important metric. A cycle renaming or dropping one would produce a
    ledger whose old rows and new rows plot different things on the same
    axis, and the graph would look fine."""
    assert SCORE_KEYS == (
        ("going", "How it's going"),
        ("effectiveness", "How effective"),
        ("feeling", "Overall feeling"),
    )


# --- the validator -----------------------------------------------------


def test_a_complete_row_is_accepted():
    validate_row(row())


@pytest.mark.parametrize(
    "bad, expected",
    [
        (row(date="14-08-2026"), "date must be YYYY-MM-DD"),
        (row(date="2026-02-30"), "not a real date"),
        (row(cycle=0), "cycle must be a positive integer"),
        (row(cycle="181"), "cycle must be a positive integer"),
        (row(cycle=True), "cycle must be a positive integer"),
        (row(scores={"going": 7, "effectiveness": 6}), "scores is missing feeling"),
        (
            row(scores={"going": 7, "effectiveness": 6, "feeling": 8, "mood": 4}),
            r"scores has unknown key\(s\) mood",
        ),
        (row(scores={"going": 7, "effectiveness": 6, "feeling": 11}), "must be 1-10"),
        (row(scores={"going": 7, "effectiveness": 6, "feeling": 0}), "must be 1-10"),
        (row(scores={"going": 7, "effectiveness": 6, "feeling": 8.5}), "whole number"),
        (row(scores={"going": 7, "effectiveness": 6, "feeling": True}), "whole number"),
        (row(overall="   "), "overall must be a non-empty string"),
        (row(good=None), "good must be a non-empty string"),
        (row(bad=""), "bad must be a non-empty string"),
        (row(changes="one change"), "changes must be a list"),
        (row(changes=["", "real"]), "non-empty string"),
        (row(mood="fine"), r"unknown field\(s\) mood"),
        (row(week="shipped a lot"), "week must be an object"),
        (row(week={"shipped": "a", "broke": "b", "stuck": "c"}), "week is missing change"),
        (
            row(week={"shipped": "a", "broke": "b", "stuck": "c", "change": "d", "mood": "e"}),
            r"week has unknown key\(s\) mood",
        ),
        (
            row(week={"shipped": "a", "broke": "  ", "stuck": "c", "change": "d"}),
            "week.broke must be a non-empty string",
        ),
    ],
)
def test_a_row_that_would_corrupt_the_series_is_refused(bad, expected):
    """One case per way a row can be wrong, because "it raised" is not the
    assertion -- *which* thing it refused is.

    `mood` appears twice on purpose: once inside `scores`, where it would
    add a fourth line to a three-line chart, and once at the top level,
    where it would sit in the ledger forever with nothing reading it. Both
    are the same real failure -- a later cycle inventing a column -- and
    they arrive through different doors."""
    with pytest.raises(RetroError, match=expected):
        validate_row(bad)


def test_no_score_is_the_only_optional_one():
    """Each of the three, dropped in turn, is refused by name.

    Written as a loop rather than three cases because the point is that
    there is no favoured column: `feeling` being the most important metric
    does not make the other two optional, and a validator that only
    checked the important one would pass this file's other tests."""
    for key, _ in SCORE_KEYS:
        scores = {k: 6 for k, _ in SCORE_KEYS}
        del scores[key]
        with pytest.raises(RetroError, match=f"scores is missing {key}"):
            validate_row(row(scores=scores))


def test_changes_may_be_empty_but_must_be_present():
    """A retro that chose to change nothing is a real answer; a retro that
    forgot the question is not."""
    validate_row(row(changes=[]))
    missing = row()
    del missing["changes"]
    with pytest.raises(RetroError, match="changes must be a list"):
        validate_row(missing)


# --- append ------------------------------------------------------------


def test_the_first_retro_writes_the_first_ledger():
    text = append("", row())
    assert json.loads(text) == {"retros": [row()]}


def test_a_second_retro_on_the_same_date_is_refused():
    """Two cycles waking on one Friday morning is the realistic way this
    ledger gets a duplicate, and a duplicate is invisible: the chart draws
    two marks on one x and every later "compare to the previous retro"
    silently weights that week twice."""
    first = append("", row())
    with pytest.raises(RetroError, match="already in the ledger"):
        append(first, row(cycle=182, overall="A second opinion."))


def test_a_later_date_appends_and_the_file_stays_in_time_order():
    """The row arrives out of order on purpose. The chart draws straight
    through from the first row to the last, so an unsorted ledger puts a
    mark outside the plot box -- sorting on write is what means the page
    never has to."""
    ledger = append("", row(date="2026-08-21", cycle=300))
    ledger = append(ledger, row(date="2026-08-14", cycle=181))
    dates = [entry["date"] for entry in json.loads(ledger)["retros"]]
    assert dates == ["2026-08-14", "2026-08-21"]


def test_an_unreadable_ledger_is_not_quietly_started_over():
    """The dangerous repair. "Cannot parse it, so begin a fresh one" would
    drop every retro ever written, on the one file whose entire purpose is
    comparison across weeks."""
    with pytest.raises(json.JSONDecodeError):
        append("{not json", row())


def test_an_absent_ledger_and_an_empty_one_both_read_as_no_retros():
    assert load("") == []
    assert load("   \n") == []
    assert load('{"retros": []}') == []


# --- the payload -------------------------------------------------------


def test_the_payload_carries_what_the_page_plots():
    payload = retros_payload(json.dumps(LEDGER))
    assert payload["range"] == [1, 10]
    assert payload["scoreKeys"] == [
        {"key": "going", "label": "How it's going"},
        {"key": "effectiveness", "label": "How effective"},
        {"key": "feeling", "label": "Overall feeling"},
    ]
    assert [entry["date"] for entry in payload["retros"]] == ["2026-08-07", "2026-08-14"]
    newest = payload["retros"][-1]
    assert newest["scores"] == {"going": 7, "effectiveness": 6, "feeling": 8}
    assert newest["cycle"] == 181
    assert newest["overall"] == "Steady, and finally measurable."
    assert newest["changes"] == [
        "Write the pace number into the entry when it changed the pick."
    ]


def test_the_date_becomes_a_number_the_chart_can_place():
    """1786665600000 is 2026-08-14T00:00:00Z -- checked with `date -u -d
    @1786665600` and written as a literal rather than recomputed here, since
    a conversion checked against itself passes whatever it does."""
    payload = retros_payload(json.dumps({"retros": [row()]}))
    assert payload["retros"][0]["at"] == 1786665600000
    assert payload["retros"][0]["date"] == "2026-08-14"


def test_the_payload_sorts_and_skips_rather_than_trusting_the_file():
    """The ledger is hand-edited in a vault, so the page's assumptions have
    to be made true on this side of the wire. A row with no usable date has
    no place on a time axis and is dropped rather than plotted at zero,
    which would put a mark decades to the left of everything else."""
    payload = retros_payload(json.dumps({"retros": [
        row(date="2026-08-21", cycle=300),
        {"date": "not a date", "cycle": 1},
        row(date="2026-08-14"),
    ]}))
    assert [entry["date"] for entry in payload["retros"]] == ["2026-08-14", "2026-08-21"]


def test_an_absent_ledger_is_an_empty_page_and_not_an_error():
    """This is the state the page ships in: the first retro has not run
    yet. An exception here would 502 a nav tab from the moment it appears
    until Friday morning."""
    payload = retros_payload("")
    assert payload["retros"] == []
    assert payload["scoreKeys"][0]["key"] == "going"


def test_a_ledger_that_will_not_parse_raises_rather_than_reading_as_empty():
    """Empty and broken are deliberately different answers. A page saying
    "no retrospectives yet" is the wrong way to report a corrupted file --
    it is indistinguishable from the true state and nobody investigates
    it."""
    with pytest.raises(json.JSONDecodeError):
        retros_payload("{oops")
    with pytest.raises(RetroError, match="must be a JSON object"):
        retros_payload("[]")


# --- wiring ------------------------------------------------------------


def test_the_source_fetch_reads_the_ledger_path(monkeypatch):
    """The one thing between the vault and the page, and nothing else in
    this suite would notice it pointing at the wrong document."""
    asked = []
    monkeypatch.setattr(
        nova_sources, "vault_read_path", lambda path: asked.append(path) or '{"retros": []}'
    )
    assert nova_sources.retro_ledger_json() == '{"retros": []}'
    assert asked == [nova_retro.RETRO_LEDGER_PATH]
    # The whole literal, not a suffix. The prefix is the half a typo would
    # live in, and `prompt.md` tells the retro cycle to `get` this exact
    # string -- so the two sides of that agreement are pinned here.
    assert nova_retro.RETRO_LEDGER_PATH == (
        "projects/sokrates/projects/agora/nova/resources/retro-ledger.json"
    )


def test_a_missing_document_reaches_the_shaping_as_empty_text(monkeypatch):
    monkeypatch.setattr(nova_sources, "vault_read_path", lambda path: None)
    assert nova_sources.retro_ledger_json() == ""


# --- the CLI, and the seam only the first retro ever crosses -----------


def test_the_vault_clients_not_found_line_reads_as_an_absent_ledger(tmp_path):
    """`vault_tool.py get` on a path that does not exist exits 0 and prints
    `[not found: <path>]`, measured against the real client on the real
    path on 2026-08-14, before the first retro ran.

    The documented flow redirects that straight into `ledger.json`, so the
    *first* append is handed a file containing a sentence rather than an
    empty one -- and without this it dies on "the existing ledger will not
    parse", which is the one run with no previous ledger to blame. This is
    a literal from another program, so it is pinned as a literal."""
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        "[not found: projects/sokrates/projects/agora/nova/resources/retro-ledger.json]\n",
        encoding="utf-8",
    )
    assert append_retro.main(["--ledger", str(ledger), "--row", _row_file(tmp_path)]) == 0
    assert json.loads(ledger.read_text(encoding="utf-8"))["retros"][0]["cycle"] == 181


def test_a_ledger_that_only_opens_with_the_phrase_is_not_discarded(tmp_path):
    """The counterpart, and the reason the pattern anchors its *end*: a
    document that merely opens with that sentence must not be read as
    absent, because "absent" here means "start a new file" -- and the new
    file would replace every retro ever written.

    Written as a document that begins with the sentinel, not one that
    merely contains it: `re.match` anchors the start on its own, so a test
    using a mention buried mid-file passes with or without any anchor at
    all, and pins nothing."""
    ledger = tmp_path / "ledger.json"
    before = ("[not found: retro-ledger.json]\n"
              + json.dumps({"retros": [row(date="2026-08-07", cycle=120)]}))
    ledger.write_text(before, encoding="utf-8")
    # Refused, and the file left exactly as it was. Being told a document
    # is unreadable is the right answer to a document that is; quietly
    # starting a new one is how the previous retros disappear.
    assert append_retro.main(["--ledger", str(ledger), "--row", _row_file(tmp_path)]) == 2
    assert ledger.read_text(encoding="utf-8") == before


def test_a_bad_row_exits_non_zero_and_writes_nothing(tmp_path):
    """A refusal that still rewrote the file would be worse than no
    validator, and an exit code nobody set would let the shell's `&&` carry
    a broken ledger into the vault."""
    ledger = tmp_path / "ledger.json"
    before = json.dumps({"retros": [row(date="2026-08-07", cycle=120)]})
    ledger.write_text(before, encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(row(scores={"going": 7, "effectiveness": 6, "mood": 8})),
                   encoding="utf-8")
    assert append_retro.main(["--ledger", str(ledger), "--row", str(bad)]) == 2
    assert ledger.read_text(encoding="utf-8") == before


def _row_file(tmp_path):
    path = tmp_path / "row.json"
    path.write_text(json.dumps(row()), encoding="utf-8")
    return str(path)


# --- the one screen (ideas.md #120) ------------------------------------


def test_the_one_screen_is_the_four_parts_he_asked_for():
    """Pinned as a literal, for `SCORE_KEYS`' reason and one more.

    He wrote the four parts out himself -- *"what shipped, what broke,
    what is still stuck, and the one thing you would want to change"* --
    and the card draws them in this order because that is the order he
    would read them in. A cycle renaming one would leave the old rows
    carrying a key nothing draws, and the card would look complete with a
    section silently missing."""
    assert WEEK_KEYS == (
        ("shipped", "What shipped"),
        ("broke", "What broke"),
        ("stuck", "What is still stuck"),
        ("change", "The one thing I would change"),
    )


def test_every_part_of_the_one_screen_is_required_by_name():
    """Each of the four, dropped in turn, refused by name.

    Written as a loop for `test_no_score_is_the_only_optional_one`'s
    reason: there is no favoured part, and a week where nothing broke is
    a sentence saying so, not an absent field. The whole block missing is
    covered separately -- that is a retro that forgot the screen, and it
    is the failure this validator exists to make impossible."""
    for key, _ in WEEK_KEYS:
        week = {k: "something real" for k, _ in WEEK_KEYS}
        del week[key]
        with pytest.raises(RetroError, match=f"week is missing {key}"):
            validate_row(row(week=week))
    missing = row()
    del missing["week"]
    with pytest.raises(RetroError, match="week must be an object"):
        validate_row(missing)


def test_the_payload_carries_the_one_screen_the_card_draws():
    payload = retros_payload(json.dumps(LEDGER))
    assert payload["weekKeys"] == [
        {"key": "shipped", "label": "What shipped"},
        {"key": "broke", "label": "What broke"},
        {"key": "stuck", "label": "What is still stuck"},
        {"key": "change", "label": "The one thing I would change"},
    ]
    newest = payload["retros"][-1]["week"]
    assert newest["change"] == (
        "Ask whether an instrument has ever returned a value before believing it."
    )
    assert set(newest) == {key for key, _ in WEEK_KEYS}


def test_a_retro_written_before_the_one_screen_existed_carries_none():
    """The three retros already in the vault have no `week` block, and the
    page has to be able to tell that from a summary it failed to read.

    `None` rather than four empty strings, because the card decides
    whether to draw at all on this value -- four empty strings would
    render as this week's report with every section blank, which is worse
    than the page saying nothing yet."""
    old = row()
    del old["week"]
    payload = retros_payload(json.dumps({"retros": [old]}))
    assert payload["retros"][0]["week"] is None


def test_half_a_summary_is_no_summary_rather_than_half_a_card():
    """A block missing a part cannot come from this loop -- the validator
    refuses it -- so it is hand-edited or damaged, and the honest reading
    is that there is nothing to draw."""
    partial = row(week={"shipped": "a", "broke": "b", "stuck": "c"})
    payload = retros_payload(json.dumps({"retros": [partial]}))
    assert payload["retros"][0]["week"] is None


def test_the_page_draws_exactly_the_parts_the_server_defines():
    """`WEEK_KEYS` is the single source of truth and the browser cannot
    import it, so the wire is the payload: `app.js` iterates
    `payload.weekKeys` rather than naming the four parts itself.

    Asserted as an absence, which is the only form this can take -- the
    failure being guarded is a later cycle hard-coding the labels into the
    card, at which point renaming a part on this side changes the ledger
    and not the screen, silently."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "app.js"
    text = source.read_text(encoding="utf-8")
    start = text.index("function renderWeekCard(")
    body = text[start:text.index("\n  }", start)]
    assert "payload.weekKeys" in body, "the card no longer reads its labels from the server"
    for _key, label in WEEK_KEYS:
        assert label not in body, f"the card hard-codes {label!r} instead of reading weekKeys"


# --- the two sides of one key list, in two languages -------------------


def test_the_page_styles_exactly_the_scores_the_server_defines():
    """`SCORE_KEYS` is the stated single source of truth and the browser
    cannot import it. `app.js` maps each key to a colour and a stroke
    width, and falls back to the "going" style for anything unknown -- so a
    key added or renamed on this side would draw a fourth line identical to
    the first, with every browser test still green.

    Read out of the file as text because that is the only wire between the
    two: nothing else in this repo would notice them disagreeing."""
    import re as _re
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "app.js"
    block = _re.search(r"var RETRO_SERIES = \{(.*?)\n  \};", source.read_text(encoding="utf-8"), _re.S)
    assert block, "RETRO_SERIES is gone from app.js, or no longer looks like an object literal"
    styled = _re.findall(r"^\s{4}(\w+):", block.group(1), _re.M)
    assert styled == [key for key, _ in SCORE_KEYS]
