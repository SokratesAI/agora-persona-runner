"""`tools/roll_health.py` -- captures stranded outside the rollable section.

The live shape this was written against: `nova/resources/issues.md` carried
94 captures between its frontmatter and its own `# ` title, which the roller
cannot see and the board does not render. The fixtures below are that shape
in miniature, plus the shapes that must NOT fire.
"""

import io
import re

import pytest

from tools import roll_health
from agora_runner.rolling import _body
from tools import roll_captures
from tools.roll_captures import MARKER


def document(strays=(), entries=(), title="# Nova — Issues",
             frontmatter="---\ntype: note\nstatus: capture\n---\n"):
    """A capture file, with `strays` sitting above the `## Entries` marker."""
    parts = [frontmatter]
    parts.extend("\n" + s + "\n" for s in strays)
    parts.append("\n" + title + "\n")
    parts.append(MARKER)
    parts.extend("\n" + e + "\n" for e in entries)
    parts.append("\n## Retired\n\n- an old one\n\n# Details\n\n- a detail\n")
    return "".join(parts)


ARCHIVE = "---\ntype: log\n---\n\n# Nova — Issues Archive\n\n## Entries\n"


def test_a_capture_above_the_marker_is_stranded():
    live = document(strays=["- 2026-08-31 (Cycle 733) — one",
                            "- 2026-08-30 (Cycle 700) — two"],
                    entries=["- 2026-08-29 (Cycle 600) — three"])
    assert roll_health.stranded_bullets(live) == [
        "- 2026-08-31 (Cycle 733) — one",
        "- 2026-08-30 (Cycle 700) — two",
    ]


def test_a_tidy_file_strands_nothing():
    live = document(entries=["- 2026-08-29 (Cycle 600) — three"])
    assert roll_health.stranded_bullets(live) == []


def test_a_yaml_list_in_the_frontmatter_is_not_a_stranded_capture():
    """`tags:` written as a block list puts `- ` lines above the marker.

    Written un-indented on purpose: YAML allows a sequence at the parent
    key's own column, and an indented one would not start with `- ` at all,
    so the fixture would pass whether or not the frontmatter were stripped.
    """
    live = document(entries=["- 2026-08-29 (Cycle 600) — three"],
                    frontmatter="---\ntype: note\ntags:\n- agora\n- nova\n---\n")
    assert roll_health.stranded_bullets(live) == []


def test_bullets_below_the_section_are_not_counted():
    """`## Retired` and `# Details` hold bullets legitimately."""
    live = document(entries=["- 2026-08-29 (Cycle 600) — three"])
    assert "- an old one" in live and "- a detail" in live
    assert roll_health.stranded_bullets(live) == []


def test_span_names_the_oldest_and_newest_cycle():
    assert roll_health.span(["- 2026-08-31 (Cycle 733) — one",
                             "- 2026-08-01 (Cycle 355) — two"]) == \
        "Cycle 355 to Cycle 733"


def test_span_says_so_when_nothing_carries_a_marker():
    assert "none carrying" in roll_health.span(["- an undated note"])


def test_inspect_reports_a_refusal_instead_of_guessing_whether_a_roll_is_owed():
    """A mis-ordered file makes the roller raise; `owed` is unknowable then."""
    live = document(entries=["- 2026-08-01 (Cycle 100) — old",
                             "- 2026-08-29 (Cycle 600) — new"])
    stranded, refusal, owed = roll_health.inspect(live, ARCHIVE)
    assert refusal is not None and "newest-first" in refusal
    assert owed is None


def test_inspect_says_a_roll_is_owed_past_keep():
    entries = [f"- 2026-08-29 (Cycle {900 - i}) — n{i}"
               for i in range(roll_health.roll_captures.KEEP + 3)]
    stranded, refusal, owed = roll_health.inspect(document(entries=entries), ARCHIVE)
    assert refusal is None and owed is True and stranded == []


def test_inspect_says_no_roll_is_owed_under_keep():
    entries = [f"- 2026-08-29 (Cycle {900 - i}) — n{i}" for i in range(3)]
    stranded, refusal, owed = roll_health.inspect(document(entries=entries), ARCHIVE)
    assert refusal is None and owed is False


def _fetch_from(docs):
    return lambda path: docs.get(path)


def test_a_stranded_capture_exits_2_and_names_the_file():
    live_path, archive_path = roll_health.PAIRS[0]
    docs = {live_path: document(strays=["- 2026-08-31 (Cycle 733) — one"],
                                entries=["- 2026-08-29 (Cycle 600) — three"]),
            archive_path: ARCHIVE}
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from(docs))
    assert unreadable == [] and clean == []
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    printed = out.getvalue()
    assert live_path in printed
    assert "1 capture(s) sit above" in printed


def test_a_document_that_could_not_be_read_exits_1_and_not_0():
    live_path, archive_path = roll_health.PAIRS[0]
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from({}))
    assert unreadable == [live_path] and findings == [] and clean == []
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 1
    assert "no instrument" in out.getvalue()


def test_a_missing_archive_alone_is_still_unreadable():
    """`get` prints `[not found:]` and exits 0, so a half-read pair must not
    be judged as a clean one."""
    live_path, archive_path = roll_health.PAIRS[0]
    docs = {live_path: document(entries=["- 2026-08-29 (Cycle 600) — three"])}
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from(docs))
    assert unreadable == [archive_path] and clean == []


def test_a_clean_pair_exits_0_and_prints_what_it_did_not_judge():
    live_path, archive_path = roll_health.PAIRS[0]
    docs = {live_path: document(entries=["- 2026-08-29 (Cycle 600) — three"]),
            archive_path: ARCHIVE}
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from(docs))
    assert findings == [] and unreadable == []
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 0
    assert "Partly judged below the section" in out.getvalue()


def test_roll_health_is_in_preflight():
    from tools import preflight
    assert "roll_health" in preflight.CHECKS


def _board(rows, details):
    """A live capture file whose `## Board` and `# Details` are real.

    `document()` above writes a `# Details` section holding one bullet and no
    `### #N` block at all, which is the shape `writeups` must answer `None`
    for -- so a fixture with real write-ups has to be built separately rather
    than by passing another argument to it.
    """
    head = ["---", "type: note", "---", "", "# Nova — Issues", MARKER.strip(),
            "", "- 2026-08-29 (Cycle 600) — a capture", "", "## Board", "",
            "| # | Item | Status | Updated | Priority |",
            "|---|------|--------|---------|---|"]
    for number, status in rows:
        head.append(f"| [[#{number} — T{number}\\|{number}]] | T{number} "
                    f"| {status} | 09-02 |  |")
    head += ["", "# Details", ""]
    for number, body in details:
        head += [f"### #{number} — T{number}", "", body, ""]
    return "\n".join(head) + "\n"


def test_writeups_is_none_when_the_file_has_no_write_up_blocks():
    """The precondition for the test below: `document()` has a `# Details`
    heading and no `### #N` block under it, so a `None` here is the absence
    of write-ups and not the absence of the section."""
    live = document(entries=["- 2026-08-29 (Cycle 600) — three"])
    assert "# Details" in live
    assert roll_health.writeups(live) is None


def test_writeups_counts_the_bodies_and_names_the_largest():
    live = _board([(1, "⚪ Backlog"), (2, "⚪ Backlog")],
                  [(1, "x" * 40), (2, "y" * 400)])
    marks = roll_health.writeups(live)
    assert marks["count"] == 2
    assert marks["bytes"] >= 440
    assert marks["largest"][0] == 2
    assert marks["done_count"] == 0 and marks["done_bytes"] == 0


def test_writeups_separates_a_done_rows_body_from_an_open_ones():
    live = _board([(1, "✅ Done"), (2, "⚪ Backlog")],
                  [(1, "x" * 40), (2, "y" * 400)])
    marks = roll_health.writeups(live)
    assert marks["count"] == 2 and marks["done_count"] == 1
    assert 0 < marks["done_bytes"] < marks["bytes"]


def test_the_report_says_how_little_the_capture_roll_moves():
    """The finding this was built for: `owed` is true, and the roll it names
    moves a rounding error against the write-ups nothing rolls."""
    live_path, archive_path = roll_health.PAIRS[0]
    entries = _entries_past_keep()
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    live = live.replace("- 2026-08-29 (Cycle 600) — a capture", entries)
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    printed = out.getvalue()
    assert "A roll is owed" in printed
    moved = int(re.search(r"The capture roll moves ([\d,]+) of", printed)
                .group(1).replace(",", ""))
    # A real, non-zero number that is nonetheless small against the file --
    # `in printed` alone passes on a hardcoded 0, which is the whole claim.
    assert 0 < moved < len(live) // 2
    assert "write-up bodies across 1 row(s)" in printed
    assert "would move nothing" in printed
    assert "largest single write-up is row #1" in printed


def test_the_report_points_at_roll_done_details_when_one_would_move():
    live_path, archive_path = roll_health.PAIRS[0]
    entries = _entries_past_keep()
    live = _board([(1, "✅ Done")], [(1, "y" * 4000)])
    live = live.replace("- 2026-08-29 (Cycle 600) — a capture", entries)
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    printed = out.getvalue()
    assert "tools.roll_done_details" in printed
    assert "would move nothing" not in printed


def test_a_clean_file_still_says_what_it_is_made_of():
    """The hole the first version left: `Rollable` says nothing about the
    62,801 bytes of write-ups that survive the roll, and the whole finding
    disappeared the moment the roll it named was actually run."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    assert findings == [] and unreadable == []
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 0
    printed = out.getvalue()
    assert "Rollable" in printed
    assert "write-up bodies across 1 row(s)" in printed
    assert "largest single write-up is row #1" in printed


def test_the_clean_summary_line_is_last_and_carries_the_write_up_weight():
    """`preflight` collapses an exit-0 check to its last line holding a
    digit, so a decomposition printed above the tail note is invisible on a
    normal morning."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 0
    lines = [ln for ln in out.getvalue().split("\n") if ln.strip()]
    last_with_digit = [ln for ln in lines if re.search(r"\d", ln)][-1]
    assert "write-ups no roller moves" in last_with_digit
    assert "0 of 1 on a done row" in last_with_digit


def _entries_past_keep(count=None, pad=1200):
    """Captures past `KEEP` whose section is over `SECTION_CEILING`.

    The padding is the point rather than filler. Since `steady()` an owed
    roll only raises when the `## Entries` section has outgrown the size
    `KEEP` stands in for, so a fixture of 62 one-line captures now reports
    steady state -- correctly, and it is what the live files do. A test about
    the remedy block has to hand the check a section that is genuinely too
    big, which is the only shape that still asks for one.
    """
    count = roll_health.roll_captures.KEEP + 2 if count is None else count
    return "\n".join(f"- 2026-08-29 (Cycle {900 - i}) — n{i} {'x' * pad}"
                     for i in range(count))


def test_an_owed_roll_prints_the_command_that_clears_it():
    """The gap this closes: `roll_health` sat at ACT in `preflight` for days
    naming a roll nobody ran, while `recap_health` -- which prints its two
    commands -- got run. Both halves of the pair have to be named, because
    the block is a paired compare-and-swap and a `put` of the live half onto
    the archive path is the one mistake that loses captures."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    live = live.replace("- 2026-08-29 (Cycle 600) — a capture",
                        _entries_past_keep())
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    printed = out.getvalue()
    assert "python3 -m tools.roll_captures" in printed
    assert f"put '{archive_path}'" in printed
    assert f"put '{live_path}' " in printed
    assert "--allow-shrink" in printed
    # The archive is written first: stopping between the two writes must be
    # able to duplicate a capture, never to lose one.
    assert printed.index(f"put '{archive_path}'") < printed.index(
        f"put '{live_path}' ")


def test_the_normalise_step_appears_only_when_the_order_is_the_blocker():
    """`--mode strays` on a file already in order is a no-op that still has
    to be explained to whoever pastes the block, so it is emitted off the
    roller's own refusal text rather than unconditionally."""
    live_path, archive_path = roll_health.PAIRS[0]
    ordered = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    ordered = ordered.replace("- 2026-08-29 (Cycle 600) — a capture",
                              _entries_past_keep())
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: ordered, archive_path: ARCHIVE}))
    out = io.StringIO()
    roll_health.report(findings, unreadable, clean, held, out=out)
    assert "normalise_captures" not in out.getvalue()

    # The live shape from 2026-09-06: one stray marker at the bottom of an
    # otherwise descending section, which is what the roller refuses on.
    strayed = ordered.replace(
        "- 2026-08-29 (Cycle 839) — n61",
        "- 2026-08-29 (Cycle 839) — n61\n- 2026-09-04 (Cycle 900) — stray")
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: strayed, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    printed = out.getvalue()
    assert roll_health.NOT_NEWEST_FIRST in printed
    assert "python3 -m tools.normalise_captures" in printed
    assert "--mode strays" in printed


def test_a_clean_pair_is_handed_no_command():
    """A remedy printed under a pair with nothing wrong is noise that trains
    a reader to skip the block on the morning it matters."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 0
    assert "vault_tool.py get" not in out.getvalue()


def test_the_command_names_the_pair_it_was_handed_not_the_module_default():
    """`check` takes `pairs`, so reading the archive back off `PAIRS` would
    print the wrong path for exactly the callers that pass their own."""
    live_path = "projects/somewhere/else/issues.md"
    archive_path = "projects/somewhere/else/issues-archive.md"
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    live = live.replace("- 2026-08-29 (Cycle 600) — a capture",
                        _entries_past_keep())
    findings, unreadable, clean, held = roll_health.check(
        pairs=((live_path, archive_path),),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    printed = out.getvalue()
    assert f"get '{live_path}'" in printed
    assert f"get '{archive_path}'" in printed
    assert roll_health.PAIRS[0][1] not in printed


def test_a_stranded_only_finding_is_not_handed_the_roll_command():
    """The roll block does not fix a capture written above the marker -- it
    would run, exit 0, and leave every stranded bullet exactly where it is.
    Printing it here would read as the remedy for a finding it cannot touch,
    so the guard is on `refusal or owed` and not on `findings` at all.
    Asserting the hand repair is still printed is the other half: this must
    fail because the *command* is absent, never because the finding is."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = document(strays=["- 2026-08-31 (Cycle 733) — stranded"],
                    entries=["- 2026-08-29 (Cycle 600) — three"])
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    printed = out.getvalue()
    assert "sit above the" in printed and "Repair: move them" in printed
    assert "vault_tool.py get" not in printed
    assert "tools.roll_captures --live" not in printed


def test_every_command_line_in_the_block_is_bare():
    """The reviewer's finding on the first version of this block: the sync
    line shipped as ``Then `python3 -m ...`: prose``, inside a block whose
    own header says to run it as one shell call. Pasted, bash substitutes
    the backticks -- the sync runs -- then tries to execute the word `Then`
    and exits 127. A substring assertion on the command passes either way,
    so this asserts the *shape*: a line carrying a command carries nothing
    else, and no line in the block has a backtick in it at all."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    live = live.replace("- 2026-08-29 (Cycle 600) — a capture",
                        _entries_past_keep())
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    block = roll_health.remedy(live_path, archive_path, None)
    assert "`" not in "\n".join(block)
    commands = [ln.strip() for ln in block
                if ln.strip().startswith(("cd ", "&& ", "python3 "))]
    for line in commands:
        assert not line.endswith(":"), line
        # A continuation ends in `\`; the last command in a chain ends in
        # neither, and no command line may carry English after it.
        assert " the " not in line, line


def _steady_pair():
    """A live file over `KEEP` whose section is comfortably inside the ceiling.

    This is the live shape, in miniature: `issues.md` sits one or two captures
    over `KEEP` on a 24,685-byte section every hour of every day, because a
    cycle writes captures into it every hour of every day.
    """
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    return live.replace("- 2026-08-29 (Cycle 600) — a capture",
                        _entries_past_keep(pad=0))


def test_a_roll_owed_on_a_small_section_does_not_raise():
    """The defect: `owed` was true on every run, forever. A cycle rolled both
    files to zero owed and twenty minutes later both were over `KEEP` again,
    for a roll moving 306 bytes of 112,939."""
    live_path, archive_path = roll_health.PAIRS[0]
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: _steady_pair(), archive_path: ARCHIVE}))
    assert findings == [] and unreadable == [] and clean == []
    assert [p for p, _, _ in held] == [live_path]
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 0
    printed = out.getvalue()
    assert "STEADY STATE" in printed
    assert "A roll is owed and it is not a finding" in printed
    # The block is what makes a finding actionable. Handing one out here is
    # how a check teaches cycles to paste past it.
    assert "tools.roll_captures" not in printed
    assert "vault_tool.py put" not in printed


def test_a_steady_pair_is_still_counted_and_named():
    """A non-raising bucket that vanishes from the report is the same as a
    check that never ran -- `preflight` shows only the last line carrying a
    digit, so the count has to be in it."""
    live_path, archive_path = roll_health.PAIRS[0]
    out = io.StringIO()
    roll_health.report(*roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: _steady_pair(), archive_path: ARCHIVE})),
        out=out)
    last = out.getvalue().rstrip().split("\n")[-1]
    assert "Swept 1 capture file(s), 1 owing a steady-state roll" in last
    assert live_path in out.getvalue()


def test_a_section_past_the_ceiling_still_raises_and_gets_its_command():
    """The failure this check exists for -- nothing runs the roller, so the
    section grows without bound -- must survive the new bucket."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    live = live.replace("- 2026-08-29 (Cycle 600) — a capture",
                        _entries_past_keep())
    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    assert held == [] and len(findings) == 1
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    assert "tools.roll_captures" in out.getvalue()


def test_the_ceiling_is_the_boundary_it_says_it_is():
    """Off-by-one on a threshold is the whole of a threshold. `<=` is what
    the report prints -- "inside the 65,000" -- so it has to be what it does."""
    assert roll_health.steady({"section": roll_health.SECTION_CEILING})
    assert not roll_health.steady({"section": roll_health.SECTION_CEILING + 1})


def test_an_unmeasured_section_never_buys_silence():
    """A check that could not measure must not read as one that came back
    clean -- the same rule as `_fetch` returning None rather than ''."""
    assert not roll_health.steady({})
    assert not roll_health.steady({"section": None})


def test_the_steady_line_names_the_section_and_the_ceiling():
    """A verdict a reader cannot check is a verdict they have to trust. Both
    numbers the rule turns on are printed, not just its conclusion."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = _steady_pair()
    out = io.StringIO()
    roll_health.report(*roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE})), out=out)
    printed = out.getvalue()
    section = len(_body(live, roll_health.roll_captures.spec_for(live))[1])
    assert f"section is {section:,} bytes" in printed
    assert f"inside the {roll_health.SECTION_CEILING:,}" in printed


# A board plus a `# Details` section, which `document()` above deliberately
# does not build -- it was written for the stranded-above-the-marker shape and
# has no rows, so `parse_board` returns no write-ups for it at all.
def boarded(details, status="⚪ Backlog", captures=1):
    """A capture file whose `# Details` bodies are `details` -> `{n: body}`."""
    rows = "\n".join(
        f"| [[#{n} — row {n}\\|{n}]] | row {n} | {status} | 09-06 | 🟠 High |"
        for n in details)
    blocks = "\n\n".join(f"### #{n} — row {n}\n\n{body}"
                          for n, body in details.items())
    entries = "".join(f"\n- 2026-08-29 (Cycle {600 - i}) — capture {i}\n"
                      for i in range(captures))
    return ("---\ntype: note\nstatus: capture\n---\n\n# Nova — Issues\n"
            + MARKER + entries
            + "\n## Board\n\n| # | Item | Status | Updated | Priority |\n"
            "|---|------|--------|---------|---|\n" + rows
            + "\n\n# Details\n\n" + blocks + "\n")


# The real thing, trimmed: `issues.md` row #5's own paragraph, then the
# capture pile an unmarked append dropped on top of it. Written out rather
# than generated, so the test cannot pass by re-spelling the rule.
PILE = """`do_HEAD` is a stub that ignores the path entirely, returning 200.

- DONE (Cycle 411): **wrong — `gitleaks` was at `/tmp/gitleaks` the whole time.**
- DONE (Cycle 400): 2026-08-25 (Cycle 398) — **There is a Crossplane managed `Repository`.**
- The bridge Dockerfile pins `CLAUDE_CODE_VERSION=2.1.226`; upstream is 2.1.245 — 2026-08-25
- 2026-08-27 (Cycle 508) — **`tools.board_status --file` takes a local path.**
"""

PROSE = """Measured 2026-09-06 06:00 Oslo by the architecture run.

The agora pod is using 499m of a 500m CPU limit and 78.7% of its scheduling
periods are throttled. What degrades is the latency of Edvard's chat.
"""


def test_a_write_up_that_is_mostly_bullets_is_a_buried_capture_pile():
    live = boarded({5: PILE, 30: PROSE})
    marks = roll_health.writeups(live)
    assert [row for row, _, _ in marks["buried"]] == [5]
    row, bullets, body = marks["buried"][0]
    assert bullets > body // 2 and bullets == roll_health.bullet_bytes(PILE)


def test_an_ordinary_prose_write_up_is_not_buried():
    # The precondition this negative depends on: the row really is there to
    # be judged, so a pass cannot come from `writeups` seeing nothing.
    live = boarded({30: PROSE})
    marks = roll_health.writeups(live)
    assert marks["count"] == 1
    assert marks["buried"] == []


def test_a_nested_bullet_under_a_step_is_not_counted():
    body = "Three fixes, in this order:\n\n1. Drop the field.\n  - and its test\n"
    assert roll_health.bullet_bytes(body) == 0


def test_a_buried_pile_raises_even_when_the_roll_is_steady_state():
    live_path, archive_path = roll_health.PAIRS[0]
    live = boarded({5: PILE}, captures=roll_captures.KEEP + 1)
    docs = {live_path: live, archive_path: ARCHIVE}
    # The precondition, asserted rather than assumed: without the buried pile
    # this pair lands in `held`, which prints and does not raise. So a pass
    # here really is the new clause and not an empty `held` bucket.
    plain = boarded({30: PROSE}, captures=roll_captures.KEEP + 1)
    _, _, _, plain_held = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: plain, archive_path: ARCHIVE}))
    assert len(plain_held) == 1

    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from(docs))
    assert held == [] and clean == [] and len(findings) == 1
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    printed = out.getvalue()
    assert "BURIED CAPTURES — row #5" in printed
    assert "not a write-up" in printed


def test_a_buried_pile_is_a_finding_when_nothing_else_is_wrong():
    """The pile alone raises — no stranded captures, no refusal, no owed roll.

    Mutation 2 of five: dropping `or entombed` from the finding branch
    survived the test above, because that file also owed a roll. This is the
    case where the pile is the only thing wrong, and it is the real one —
    `issues.md` owes a roll every hour, `ideas.md` need not.
    """
    live_path, archive_path = roll_health.PAIRS[0]
    live = boarded({5: PILE}, captures=1)
    docs = {live_path: live, archive_path: ARCHIVE}
    # Precondition: the three older signals are all quiet on this document.
    stranded, refusal, owed = roll_health.inspect(live, ARCHIVE)
    assert stranded == [] and refusal is None and owed is False

    findings, unreadable, clean, held = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from(docs))
    assert clean == [] and held == [] and len(findings) == 1
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, held, out=out) == 2
    assert "BURIED CAPTURES — row #5" in out.getvalue()
