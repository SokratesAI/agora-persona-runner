"""Nova's site: the parsers, and the real request path.

The write half -- what a capture does to a file -- lives in
test_nova_capture.py; what reaches it over HTTP is here.

The fixtures are real. `journal_sample.md` is five entries lifted
verbatim out of the live journal, chosen because between them they
carry all four heading formats the file has accumulated over 49 cycles
plus one entry with no cycle number at all; `digest_sample.md` is the
live digest as-is. Cycle 48 paid for that rule: a hand-trimmed fixture
is a statement about what someone thought mattered, and the mutation it
missed was in the part they trimmed.

The HTTP tests drive `NovaSiteHandler` through `handle_one_request` on
a fake socket rather than calling the helpers underneath it, because
routing is where the bugs are and Cycle 49's bug was a test that never
let the real entry point run. A loopback request is not an option here:
tests/conftest.py blocks `socket.connect` outright, deliberately.
"""

import ast
import gzip
import importlib
import inspect
import io
import json
import pathlib
import queue
import time
import os
import re
import signal
import sys
import threading
import urllib.parse
from datetime import datetime
from unittest.mock import patch

import pytest

from agora_runner import nova_capture, nova_journal, nova_replies, nova_site, nova_sources, vault
from agora_runner.config import OSLO
from agora_runner.nova_site import MIN_COMPRESS_BYTES
from agora_runner.vault import VaultFiles
from agora_runner.nova_journal import (
    JOURNAL_DIR,
    JOURNAL_PATH,
    assemble_entries,
    normalise_entry,
    synthetic_heading,
    assign_emoji,
    entry_filename,
    build_status,
    is_empty_needs,
    parse_digest,
    parse_heading,
    parse_journal,
    parse_journal_file,
    parse_board_refs,
    parse_pr_refs,
    render_blocks,
    render_inline,
    split_brief,
    strip_brief_label,
    split_entries,
    split_outcome,
    split_sentences,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(autouse=True)
def _split_the_journal_fixture_into_documents():
    """Every test that sets up a journal does it by patching
    `vault_read_path` with a whole-file fixture. This splits that fixture
    into the per-entry documents the site actually reads, so those tests
    describe today's vault rather than the one that existed before
    2026-08-09.

    It used to hand `vault_bulk_fetch` an empty folder instead, which sent
    all 27 of them down `journal_markdown`'s archive fallback. That
    fallback was deleted this cycle because `journal.md` was emptied on
    2026-08-10 and it could only ever return zero entries -- so the tests
    were pinning a branch production can no longer take, and a test suite
    that green-lights a dead path is how the next cycle learns to call it.
    The split is `tools.split_journal.plan`, the same one the migration
    used, and `test_the_split_reassembles_into_an_identical_entry_list`
    pins that it parses to exactly what the monolith parsed to -- so
    nothing below changes meaning by arriving this way.

    Reading the markdown through `nova_sources.vault_read_path` at call
    time rather than capturing it here is what keeps each test's own
    `patch.object` in charge: whatever it installs is what gets split,
    including a `None` (an empty vault) and a `side_effect` that raises
    (a database that would not answer, which must still reach the caller
    as an error).

    Tests that patch `vault_bulk_fetch` themselves override this, and the
    network stays blocked either way -- conftest refuses it outright.

    `vault_list_ids` is here for the same reason -- it is the other half of
    the same lookup -- and `_ensure_worker` is here because patching the
    reads alone is not actually enough. A few tests below POST a real
    comment, which starts the real reply worker on a *background thread*,
    which then answers whenever it gets round to it. That is usually after
    the test has finished and taken its patches with it, so the vault read
    escapes to the network in maybe one run in three: a flake that would
    have been blamed on whatever test happened to be running at the time.
    Not starting the thread is the only version of this that is not a
    race. Tests that care what `enqueue_reply` was called with patch it
    themselves, which wins over this."""
    from tools.split_journal import plan

    def _bulk(prefix, with_mtimes=False):
        markdown = nova_sources.vault_read_path(JOURNAL_PATH) if prefix == JOURNAL_DIR else None
        files = VaultFiles(plan(markdown) if markdown else {})
        return (files, {}) if with_mtimes else files

    with patch.object(nova_sources, "vault_bulk_fetch", side_effect=_bulk), \
            patch.object(nova_sources, "vault_list_ids", return_value=[]), \
            patch.object(nova_replies, "_ensure_worker"):
        yield
        # With no worker, a test that posted a real comment leaves its item
        # in the module-level queue for the rest of the session. Nothing
        # asserts on that state today, so this is housekeeping rather than a
        # fix -- but it is module state shared with test_nova_replies.py,
        # and "inert" is a property of the current tests, not of the file.
        nova_replies._queue = queue.Queue()
        nova_replies._pending = {}
        nova_replies._failed = {}


@pytest.fixture(scope="module")
def journal_md():
    return _fixture("journal_sample.md")


@pytest.fixture(scope="module")
def digest_md():
    return _fixture("digest_sample.md")


@pytest.fixture(scope="module")
def entries(journal_md):
    return parse_journal_file(journal_md)


# --- headings -------------------------------------------------------------


@pytest.mark.parametrize(
    "heading,cycle,date,time,title",
    [
        ("2026-08-09 04:20 (Oslo) — Cycle 49", 49, "2026-08-09", "04:20", ""),
        (
            "Cycle 29 — 2026-08-05, 14:10 Oslo — I stopped at 8% and banked the design instead",
            29,
            "2026-08-05",
            "14:10",
            "I stopped at 8% and banked the design instead",
        ),
        ("2026-08-04 — Cycle 19 (Nova)", 19, "2026-08-04", "", ""),
        (
            "2026-08-03 03:19Z — Cycle 6, closing status (two lines, so the next cycle doesn't have to guess)",
            6,
            "2026-08-03",
            "03:19",
            "closing status (two lines, so the next cycle doesn't have to guess)",
        ),
        (
            "2026-08-02 — Edvard's first message (not a cycle)",
            None,
            "2026-08-02",
            "",
            "Edvard's first message (not a cycle)",
        ),
    ],
)
def test_every_heading_format_in_the_live_journal_parses(heading, cycle, date, time, title):
    parsed = parse_heading(heading)
    assert parsed["cycle"] == cycle
    assert parsed["date"] == date
    assert parsed["time"] == time
    assert parsed["title"] == title


def test_the_trailing_Z_timezone_does_not_swallow_the_time():
    """`03:19Z` has no word boundary before the Z, so a \\b-anchored time
    pattern silently drops the whole timestamp on Cycle 6's format."""
    assert parse_heading("2026-08-03 03:19Z — Cycle 6")["time"] == "03:19"


def test_an_entry_with_no_cycle_number_is_kept_not_dropped(entries):
    orphans = [e for e in entries if e["cycle"] is None]
    assert len(orphans) == 1
    assert orphans[0]["title"] == "Edvard's first message (not a cycle)"


# --- entry bodies ---------------------------------------------------------


def test_preamble_above_the_entries_marker_is_not_an_entry(entries):
    assert all("Preamble the parser must drop" not in e["body"] for e in entries)
    assert [e["cycle"] for e in entries] == [49, 29, 19, 6, None]


def test_a_heading_in_the_preamble_does_not_become_an_entry():
    """The version of this that only used the fixture pinned nothing: the
    live preamble contains no `###`, so dropping the split changed no
    behaviour and the test still passed (mutation run, 2026-08-09). The
    preamble documents the journal's own heading format, which is exactly
    the text most likely to grow one."""
    markdown = (
        "# Journal\n\nWrite entries like:\n\n### 2026-01-01 00:00 (Oslo) — Cycle 0\n\n"
        "## Entries\n\n### 2026-08-09 01:00 (Oslo) — Cycle 1\n\nReal entry."
    )
    assert [e["cycle"] for e in parse_journal_file(markdown)] == [1]


def test_the_two_parsers_disagree_on_purpose_and_the_hourly_one_never_cuts():
    """One text, two answers, and which one is right is a fact about where
    the text came from rather than anything in it.

    This is the whole reason there are two functions instead of one with a
    flag. The body below is an entries body -- what the folder assembles
    and what every hourly caller holds -- whose newest entry quotes the
    captures marker, which `prompt.md` step 6 tells every cycle to write
    about. Read as a whole `journal.md` it loses that entry *and* every
    entry above it, silently; read as what it is, nothing is lost.

    Asserting both halves is deliberate: pinning only `parse_journal`
    would stay green if `parse_journal_file` were quietly made identical
    to it, which would take the migration's own verification with it."""
    body = (
        "### Cycle 3\n\nNewest.\n\n"
        "### Cycle 2\n\nI appended my captures under:\n\n## Entries\n\nis the marker.\n\n"
        "### Cycle 1\n\nOldest.\n"
    )
    assert [e["cycle"] for e in parse_journal(body)] == [3, 2, 1]
    assert [e["cycle"] for e in parse_journal_file(body)] == [1]


def test_only_the_migration_reads_a_whole_journal_file():
    """The guard on the misdeclaration this split exists to prevent.

    `parse_journal_file` cuts at the captures marker, which is correct for
    exactly one source -- the frozen `journal.md` -- and destructive for
    every other. Nothing that runs every hour holds one of those, so the
    honest invariant is not "call it carefully", it is "there is one
    caller". A new one is a design decision that should have to delete
    this test, rather than a line that reads fine in review.

    Cycle 154 threaded a `strip_header` flag through instead, and the
    hazard it left behind was measured here at Cycle 156: 35 call sites in
    this suite, not one of them passing the flag, and 34 holding an
    entries body. The tests modelled the wrong combination as normal,
    which is where a future caller would have copied it from."""
    root = pathlib.Path(__file__).resolve().parent.parent
    callers = set()
    for path in list((root / "agora_runner").rglob("*.py")) + list((root / "tools").rglob("*.py")):
        if "parse_journal_file" in path.read_text(encoding="utf-8"):
            callers.add(path.relative_to(root).as_posix())
    assert callers == {"agora_runner/nova_journal.py", "tools/split_journal.py"}


def test_footer_is_lifted_out_of_the_body(entries):
    latest = entries[0]
    assert latest["pr"] == "bridge#22"
    assert latest["outcome"] == "merged"
    assert "PR: bridge#22" not in latest["body"]
    assert not latest["body"].rstrip().endswith("---")


def test_a_rule_inside_an_entry_is_not_mistaken_for_the_footer():
    body = (
        "Opening paragraph.\n\n---\n\nA section after a horizontal rule, "
        "which is not the footer.\n\n---\nPR: #12 | Outcome: shipped"
    )
    entry = parse_journal_file("## Entries\n\n### 2026-08-09 01:00 (Oslo) — Cycle 1\n\n" + body)[0]
    assert entry["pr"] == "#12"
    assert entry["outcome"] == "shipped"
    assert "A section after a horizontal rule" in entry["body"]


def test_an_entry_with_no_footer_still_parses():
    entry = parse_journal_file("## Entries\n\n### 2026-08-09 01:00 (Oslo) — Cycle 1\n\nJust prose.")[0]
    assert entry["pr"] == ""
    assert entry["outcome"] == ""
    assert entry["body"] == "Just prose."


def test_a_footer_without_its_rule_still_parses():
    """Cycle 104's real shape: the `Reviewer:` line took the rule's place.

    Its card showed no PR and no outcome for a cycle that merged #88.
    """
    body = "The account of the cycle.\n\nReviewer: 2 findings, 2 acted on\nPR: #88 | Outcome: merged"
    entry = parse_journal_file("## Entries\n\n### 2026-08-09 01:00 (Oslo) — Cycle 1\n\n" + body)[0]
    assert entry["pr"] == "#88"
    assert entry["outcome"] == "merged"
    assert "PR: #88" not in entry["body"]
    assert entry["body"].endswith("Reviewer: 2 findings, 2 acted on")


# --- outcomes -------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome,label,detail",
    [
        ("merged", "merged", ""),
        ("no-op", "no-op", ""),
        ("stuck — CI outage, merged nothing", "stuck", "CI outage, merged nothing"),
        ("shipped (vault-only)", "shipped", "vault-only"),
        (
            "no-op (verification: bridge#14 already covers Next-cycle item 1)",
            "no-op",
            "verification: bridge#14 already covers Next-cycle item 1",
        ),
        ("merged — but see the incident above", "merged", "but see the incident above"),
        ("", "", ""),
    ],
)
def test_a_qualified_outcome_splits_into_a_badge_and_its_detail(outcome, label, detail):
    assert split_outcome(outcome) == (label, detail)


def test_no_qualifier_is_ever_dropped(entries):
    """The badge is a rendering choice; losing the sentence beside it
    would be a truncation, and this file does not truncate."""
    for entry in entries:
        assert entry["outcome"] == entry["outcome"].strip()
        assert "(" not in entry["outcome"]


def test_the_live_journals_qualified_outcomes_survive_the_round_trip():
    label, detail = split_outcome(
        "shipped (vault-only: issues #34 boarded and done, digest reformatted, "
        "identity.md + prompt.md path fixed)"
    )
    assert label == "shipped"
    assert detail.startswith("vault-only: issues #34 boarded")
    assert detail.endswith("path fixed")


# --- rendering ------------------------------------------------------------


def test_hard_wrapped_lines_are_joined_into_one_paragraph():
    blocks = render_blocks("The journal is wrapped\nat ninety-five columns\n\nSecond paragraph.")
    assert [b["type"] for b in blocks] == ["p", "p"]
    assert blocks[0]["spans"][0]["text"] == "The journal is wrapped at ninety-five columns"


def test_fenced_code_keeps_its_line_breaks():
    blocks = render_blocks("Intro.\n\n```\none\ntwo\n```\n\nOutro.")
    assert [b["type"] for b in blocks] == ["p", "code", "p"]
    assert blocks[1]["text"] == "one\ntwo"


def test_bullets_become_list_blocks():
    blocks = render_blocks("Lead in.\n\n- first\n- second\n\nAfter.")
    assert [b["type"] for b in blocks] == ["p", "li", "li", "p"]
    assert blocks[1]["spans"][0]["text"] == "first"


def test_inline_code_and_bold_are_separate_spans():
    spans = render_inline("Set `NOVA_PORT` and the **site** answers.")
    assert [(s["kind"], s["text"]) for s in spans] == [
        ("text", "Set "),
        ("code", "NOVA_PORT"),
        ("text", " and the "),
        ("strong", "site"),
        ("text", " answers."),
    ]


def test_asterisks_inside_backticks_stay_literal():
    """`**kwargs` and glob patterns are ordinary journal content."""
    spans = render_inline("pass `**kwargs` through")
    assert [(s["kind"], s["text"]) for s in spans] == [
        ("text", "pass "),
        ("code", "**kwargs"),
        ("text", " through"),
    ]


def test_an_attachment_becomes_a_span_with_its_url_split_out():
    """The owner's uploaded picture, in a board write-up rather than a comment.

    A board comment is appended to the row's own write-up, which reaches
    the page as server-parsed spans -- so until this existed, the file he
    had just attached rendered as the literal `![shot.png](/api/upload/…)`
    text of the markdown line. `url` is a separate field because app.js
    builds every node with `textContent`.
    """
    spans = render_inline("before ![shot.png](/api/upload/ab12.png) after")
    assert [(s["kind"], s["text"]) for s in spans] == [
        ("text", "before "),
        ("attach", "shot.png"),
        ("text", " after"),
    ]
    assert spans[1]["url"] == "/api/upload/ab12.png"
    assert spans[1]["isImage"] is True


def test_an_attached_file_is_marked_as_not_an_image():
    """No bang means a PDF, and the page paints a paperclip, not a thumbnail."""
    spans = render_inline("[notes.pdf](/api/upload/cd34.pdf)")
    assert spans[0]["kind"] == "attach"
    assert spans[0]["isImage"] is False


def test_a_link_we_did_not_write_stays_text():
    """The path must start `/api/upload/`, which is the whole safety rule.

    A pasted `[x](javascript:…)` or a remote tracker URL is not a thing
    this site generated, so it is shown as the characters it is rather
    than becoming an element with an href.
    """
    for text in (
        "[x](javascript:alert(1))",
        "[x](https://tracker.example/pixel.gif)",
        "![x](/api/uploads/ab12.png)",
    ):
        spans = render_inline(text)
        assert all(s["kind"] == "text" for s in spans), text
        assert "".join(s["text"] for s in spans) == text


def test_bold_inside_a_filename_cannot_split_the_attachment():
    """The construct is matched before `**`, so the whole line survives."""
    spans = render_inline("![a**b**c.png](/api/upload/ab12.png)")
    assert [s["kind"] for s in spans] == ["attach"]
    assert spans[0]["text"] == "a**b**c.png"


def test_markup_in_the_vault_stays_text():
    """The client builds nodes with textContent, so the contract this has
    to keep is that a span carries the raw characters and the renderer
    never emits a markup string of its own."""
    spans = render_inline("a <script>alert(1)</script> tag")
    assert "".join(s["text"] for s in spans) == "a <script>alert(1)</script> tag"
    assert all(s["kind"] == "text" for s in spans)


def test_the_real_journal_renders_every_entry_to_at_least_one_block(entries):
    for entry in entries:
        assert render_blocks(entry["body"]), f"cycle {entry['cycle']} rendered empty"


# --- digest ---------------------------------------------------------------


def test_digest_lines_are_parsed_from_the_live_file(digest_md):
    digest = parse_digest(digest_md)
    cycles = [line["cycle"] for line in digest["lines"]]
    assert cycles == sorted(cycles, reverse=True)
    assert digest["lines"][0]["cycle"] >= 49
    assert digest["lines"][0]["at"].startswith("2026-")
    assert "\n" not in digest["lines"][0]["text"]


def test_next_cycle_section_is_carried_through(digest_md):
    assert "PWA" in parse_digest(digest_md)["nextCycle"]


# --- the digest archive ---------------------------------------------------
#
# `journal-digest.md` reached 100KB, 97KB of which was 54 old digest
# lines, so the old ones now roll off into a second file. These pin the
# join rather than the rolling: the site has to keep showing every line
# it ever showed, and the archive must not be able to shout over the two
# short sections at the top that are the whole reason the file is small.

_LIVE_DIGEST = """## Needs Edvard

Should the node be replaced?

## Next cycle

Check the deploy.

## Digest

**Cycle 109** (2026-08-11 13:20) — The newest one.

**Cycle 108** (2026-08-11 12:43) — The oldest one still live.
"""

_ARCHIVED_DIGEST = """---
type: log
updated: 2026-08-11
---

# Journal — Digest Archive

**Cycle 107** (2026-08-11 12:40) — Rolled off.

**Cycle 106** (2026-08-11 11:20) — Rolled off earlier.
"""


def _digest_reader(archive):
    """`vault_read_path` answering the two digest paths and nothing else."""
    def read(path):
        if path == nova_journal.DIGEST_PATH:
            return _LIVE_DIGEST
        if path == nova_journal.DIGEST_ARCHIVE_PATH:
            return archive
        raise AssertionError(f"unexpected read: {path}")
    return patch.object(nova_sources, "vault_read_path", side_effect=read)


def test_archived_digest_lines_are_still_served_after_the_live_ones():
    with _digest_reader(_ARCHIVED_DIGEST):
        lines = parse_digest(nova_sources.digest_markdown())["lines"]
    assert [line["cycle"] for line in lines] == [109, 108, 107, 106]


def test_the_archive_cannot_displace_the_sections_edvard_reads():
    # The join is concatenation, so an archive carrying its own `##`
    # heading would start a rival section and `_sections` keeps the last
    # one it sees. The archive is `#`-titled for exactly this reason.
    with _digest_reader(_ARCHIVED_DIGEST):
        digest = parse_digest(nova_sources.digest_markdown())
    assert "Check the deploy." in digest["nextCycle"]


def test_the_archives_own_frontmatter_and_title_are_not_digest_lines():
    with _digest_reader(_ARCHIVED_DIGEST):
        texts = [l["text"] for l in parse_digest(nova_sources.digest_markdown())["lines"]]
    assert not any("type: log" in t or "Archive" in t for t in texts)


def test_a_missing_archive_leaves_the_live_digest_exactly_as_it_was():
    # The split has to be safe in either deploy order: this deploying
    # before the vault file is written must be a no-op, not a trailing
    # blank line the parser has to forgive.
    for absent in (None, ""):
        with _digest_reader(absent):
            assert nova_sources.digest_markdown() == _LIVE_DIGEST


# --- status ---------------------------------------------------------------


def test_status_reports_the_newest_cycle_and_the_span_of_the_log(entries):
    status = build_status(entries)
    assert status["cycle"] == 49
    assert status["lastWokeTime"] == "04:20"
    assert status["lastOutcome"] == "merged"
    # 2026-08-02 (oldest entry) to 2026-08-09 (newest).
    assert status["runningDays"] == 7
    assert status["entryCount"] == len(entries)


def test_status_of_an_empty_journal_does_not_explode():
    status = build_status([])
    assert status["cycle"] is None
    assert status["runningDays"] == 0


# --- the real request path ------------------------------------------------


class _KeptOpenBytesIO(io.BytesIO):
    """BaseHTTPRequestHandler.finish() closes wfile; the assertions come after."""

    def close(self):
        pass


class _FakeSocket:
    """Enough socket for BaseHTTPRequestHandler.

    `sendall` is not optional: with `wbufsize = 0` (the handler's default)
    socketserver wraps the connection in a `_SocketWriter` that bypasses
    `makefile` entirely and writes straight to the socket.
    """

    def __init__(self, request_bytes):
        self._request = request_bytes
        self.sent = _KeptOpenBytesIO()

    def makefile(self, mode, *args, **kwargs):
        return io.BytesIO(self._request) if "r" in mode else self.sent

    def sendall(self, data):
        self.sent.write(data)


class _FakeServer:
    server_name = "nova-test"
    server_port = 8083


def _get(path, headers="", method="GET"):
    """`headers` is raw request-header text, each line CRLF-terminated.

    Defaulted to none so every test written before compression keeps
    describing a client that sends no `Accept-Encoding` -- which is a real
    client, not a simplification: that is exactly what urllib does.
    """
    sock = _FakeSocket(f"{method} {path} HTTP/1.1\r\nHost: nova\r\n{headers}\r\n".encode())
    nova_site.NovaSiteHandler(sock, ("127.0.0.1", 50000), _FakeServer())
    raw = sock.sent.getvalue()
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, head.decode("latin-1"), body


# What Chrome, Firefox and Safari actually put on the wire in 2026.
BROWSER_ACCEPT_ENCODING = "Accept-Encoding: gzip, deflate, br, zstd\r\n"


def _post(path, payload, content_type="application/json"):
    body = json.dumps(payload).encode()
    request = (
        f"POST {path} HTTP/1.1\r\nHost: nova\r\n"
        f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    sock = _FakeSocket(request)
    nova_site.NovaSiteHandler(sock, ("127.0.0.1", 50000), _FakeServer())
    raw = sock.sent.getvalue()
    head, _, response_body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, head.decode("latin-1"), response_body


def test_root_serves_the_shell():
    status, head, body = _get("/")
    assert status == 200
    assert "text/html" in head
    assert b'<div id="feed"' in body or b'id="feed"' in body


"""Edvard, capture 2026-08-20: "Issues and ideas takes a while to load."

Measured against the live site the same day, gzipped and on the wire:
`app.js` 76,254 bytes and `style.css` 19,637 bytes against 13,429 for the
board payload. The shell was 87KB of the ~110KB a board page cost, and it
was re-fetched in full on every navigation -- the service worker is
network-first, and no static response carried a validator, so there was
nothing for a returning client to make its request conditional on.
"""


@pytest.mark.parametrize("path", ["/app.js", "/style.css", "/", "/issues"])
def test_a_static_response_carries_a_validator(path):
    status, head, _ = _get(path)
    assert status == 200
    assert re.search(r"^ETag: ", head, re.M), f"{path} went out with no ETag"
    # Not `max-age`: the file changes on every deploy under the same URL,
    # so the only correct freshness rule is "ask me".
    assert re.search(r"^Cache-Control: no-cache", head, re.M)


@pytest.mark.parametrize("path", ["/app.js", "/style.css", "/issues"])
def test_a_returning_client_is_told_304_rather_than_sent_the_shell_again(path):
    _, head, body = _get(path)
    etag = re.search(r"^ETag: (\S+)", head, re.M).group(1)
    assert len(body) > 0
    status, head, again = _get(path, f"If-None-Match: {etag}\r\n")
    assert status == 304
    assert again == b"", "a 304 carried a body"
    # The saving is the whole point of the change, so assert it rather
    # than the status alone: a 304 that still shipped the file would pass
    # every other check here.
    assert len(again) < len(body)


def test_a_rebuilt_asset_is_not_answered_304_against_the_old_one():
    """The failure this must never have: the service worker is
    network-first precisely so a deploy cannot be pinned out, and an
    etag that did not follow the bytes would reintroduce that by the
    back door."""
    _, head, _ = _get("/app.js")
    before = re.search(r"^ETag: (\S+)", head, re.M).group(1)
    path = pathlib.Path(nova_site.PUBLIC_DIR) / "app.js"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n/* a later build */\n")
        status, head, _ = _get("/app.js", f"If-None-Match: {before}\r\n")
    finally:
        path.write_bytes(original)
    assert status == 200, "a client holding the previous build was told 304"
    assert re.search(r"^ETag: (\S+)", head, re.M).group(1) != before


def test_two_static_files_do_not_share_an_etag():
    """A validator derived from anything but the bytes -- a build stamp,
    a constant -- would pass every test above while answering `style.css`
    with `app.js`'s tag, and the client would then skip the fetch for
    whichever it asked for second."""
    _, js, _ = _get("/app.js")
    _, css, _ = _get("/style.css")
    assert re.search(r"^ETag: (\S+)", js, re.M).group(1) != \
        re.search(r"^ETag: (\S+)", css, re.M).group(1)


@pytest.mark.parametrize("path", ["/cycle/49", "/cycle/7/"])
def test_a_cycle_deep_link_resolves_on_a_cold_load(path):
    """Item 4 is only worth anything if the URL survives being pasted
    into a browser rather than reached by client-side navigation."""
    status, head, _ = _get(path)
    assert status == 200
    assert "text/html" in head


def test_static_assets_are_served_with_their_own_types():
    for path, expected in [
        ("/app.js", "javascript"),
        ("/style.css", "text/css"),
        ("/icon.svg", "image/svg+xml"),
        ("/manifest.webmanifest", "application/"),
    ]:
        status, head, body = _get(path)
        assert status == 200, path
        assert expected in head, (path, head)
        assert body


def test_unknown_paths_are_404_not_a_file_read():
    for path in ["/nope", "/api/nope", "/../config.py", "/api/journal/49"]:
        status, _, _ = _get(path)
        assert status == 404, path


def test_api_journal_returns_rendered_entries_without_the_raw_body(journal_md):
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        status, head, body = _get("/api/journal")
    assert status == 200
    assert "application/json" in head
    payload = json.loads(body)
    assert payload["status"]["cycle"] == 49
    assert len(payload["entries"]) == 5
    assert payload["entries"][0]["blocks"][0]["type"] == "p"
    assert "body" not in payload["entries"][0]


def test_api_digest_returns_the_handoff_and_the_digest_lines(digest_md):
    # Answer by path, not with one value for every read: `digest_markdown`
    # now reads the live file and the archive, and a blanket return_value
    # hands it the fixture twice. That is not a duplicate -- `_sections`
    # keeps the last `##` it sees, so the second copy silently replaces
    # the first's sections and the test passes while exercising a shape
    # the real files cannot have.
    def read(path):
        return digest_md if path == nova_journal.DIGEST_PATH else ""
    with patch.object(nova_sources, "vault_read_path", side_effect=read):
        status, _, body = _get("/api/digest")
    payload = json.loads(body)
    assert status == 200
    assert "nextCycle" in payload
    assert payload["lines"]


def test_a_missing_vault_file_is_an_empty_journal_not_a_500():
    with patch.object(nova_sources, "vault_read_path", return_value=None):
        status, _, body = _get("/api/journal")
    assert status == 200
    assert json.loads(body)["entries"] == []


def test_a_vault_failure_is_reported_as_502():
    with patch.object(nova_sources, "vault_read_path", side_effect=RuntimeError("couchdb down")):
        status, _, body = _get("/api/journal")
    assert status == 502
    assert "couchdb down" in json.loads(body)["error"]


def test_app_js_builds_no_html_from_strings():
    """app.js's header declares that it contains no innerHTML and never
    should -- the reason markup is something it cannot produce rather than
    something it must remember to escape. Until now that was a comment
    enforced by nothing, which is the same shape as the sw.js cache comment
    Cycle 50 found describing a behaviour the code did not have.

    Comments are stripped first: the header says the word "innerHTML" twice
    while forbidding it, so a naive scan reports the file as violating its
    own rule.
    """
    source = open(
        os.path.join(os.path.dirname(nova_site.PUBLIC_DIR), "nova_public", "app.js"),
        encoding="utf-8",
    ).read()
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.MULTILINE)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in code, f"{sink} reached app.js"


def test_the_only_write_verb_is_post():
    """This replaces Cycle 50's `test_the_site_is_read_only`, which asserted
    no write verb existed at all so that adding one had to be a deliberate
    act. Adding the capture box is that act. The invariant it was really
    protecting -- that the write surface stays one verb wide and visible in
    a test -- is what is pinned here instead."""
    assert hasattr(nova_site.NovaSiteHandler, "do_POST")
    for verb in ("do_PUT", "do_PATCH", "do_DELETE"):
        assert not hasattr(nova_site.NovaSiteHandler, verb)


def test_post_to_anything_but_capture_is_404():
    for path in ["/", "/api/journal", "/api/digest", "/app.js", "/api/capture/issues"]:
        status, _, _ = _post(path, {"target": "issues", "text": "x"})
        assert status == 404, path


def test_a_capture_reaches_the_vault_through_the_real_request_path():
    with patch.object(nova_site, "capture", return_value=(True, "captured to issues")) as cap:
        status, _, body = _post("/api/capture", {"target": "issues", "text": "the app needs a restart"})
    assert status == 200
    assert json.loads(body)["ok"] is True
    # The unrated default reaches `capture` explicitly rather than by
    # omission, so this pins that an unrated capture still writes his words
    # and nothing else -- tests/test_board_priority.py covers a rated one.
    cap.assert_called_once_with("issues", "the app needs a restart", "")


def test_editing_a_capture_reaches_the_vault_through_the_real_request_path():
    with patch.object(nova_site, "amend", return_value=(True, "edited in issues")) as am:
        status, _, body = _post(
            "/api/capture/edit",
            {"target": "issues", "index": 2, "original": "old wording", "text": "new wording"},
        )
    assert status == 200
    assert json.loads(body)["ok"] is True
    am.assert_called_once_with("issues", 2, "old wording", "new wording")


def test_deleting_a_capture_never_carries_replacement_text():
    """The two routes exist so that deleting cannot be reached by accident.
    Whatever a client puts in `text` on the delete route is ignored, so a
    stray field can never turn a delete into an edit."""
    with patch.object(nova_site, "amend", return_value=(True, "deleted in ideas")) as am:
        status, _, body = _post(
            "/api/capture/delete",
            {"target": "ideas", "index": 0, "original": "a typo", "text": "surprise"},
        )
    assert status == 200
    assert json.loads(body)["ok"] is True
    am.assert_called_once_with("ideas", 0, "a typo", "")


def test_an_edit_with_nothing_in_it_is_rejected_rather_than_treated_as_a_delete():
    """The dangerous shape: an edit whose text arrived blank. Answered as a
    bad request, never carried out."""
    with patch.object(nova_site, "amend") as am:
        status, _, _ = _post(
            "/api/capture/edit",
            {"target": "issues", "index": 0, "original": "keep me", "text": "   "}
        )
    assert status == 400
    am.assert_not_called()


@pytest.mark.parametrize("path", ["/api/capture/edit", "/api/capture/delete"])
@pytest.mark.parametrize("payload", [
    {"target": "../../etc/passwd", "index": 0, "original": "x"},
    {"target": "projects/sokrates/projects/nova/issues.md", "index": 0, "original": "x"},
    {"index": 0, "original": "x"},
    {"target": "issues", "index": 0},
    {"target": "issues", "index": 0, "original": ""},
    {"target": "issues", "index": 0, "original": "   "},
    {"target": "issues", "index": 0, "original": 42},
    # `True` is an int in Python and would silently address capture 1.
    {"target": "issues", "index": True, "original": "x"},
    {"target": "issues", "index": -1, "original": "x"},
    {"target": "issues", "index": "0", "original": "x"},
    {"target": "issues", "original": "x"},
])
def test_an_amend_that_could_address_the_wrong_document_is_rejected(path, payload):
    with patch.object(nova_site, "amend") as am:
        status, _, _ = _post(path, payload)
    assert status == 400
    am.assert_not_called()


def test_a_capture_that_moved_under_him_is_a_conflict_rather_than_a_failure():
    """A cycle boarded the bullet while the page was open. Nothing broke --
    the address is stale -- so the page should re-read, not retry, and 502
    would tell it the opposite."""
    with patch.object(nova_site, "amend",
                      return_value=(False, "that capture is no longer in the list")):
        status, _, body = _post(
            "/api/capture/delete", {"target": "issues", "index": 0, "original": "gone"}
        )
    assert status == 409
    assert json.loads(body)["ok"] is False


def test_a_vault_failure_on_an_amend_is_still_a_502():
    with patch.object(nova_site, "amend",
                      return_value=(False, "could not write to issues: FAILED(401)")):
        status, _, _ = _post(
            "/api/capture/delete", {"target": "issues", "index": 0, "original": "x"}
        )
    assert status == 502


@pytest.mark.parametrize("path", ["/api/capture/edit", "/api/capture/delete"])
def test_a_successful_amend_invalidates_the_board_it_changed(path):
    nova_site.reset_cache()
    nova_site._cache["board:issues"] = ({"captures": ["stale"]}, "{}", 'W/"x"', 0.0)
    with patch.object(nova_site, "amend", return_value=(True, "ok")):
        _post(path, {"target": "issues", "index": 0, "original": "x", "text": "y"})
    assert "board:issues" not in nova_site._cache, (
        "the board Edvard is looking at would reload to the pre-edit copy"
    )


def test_converting_a_capture_reaches_the_vault_through_the_real_request_path():
    with patch.object(nova_site, "convert_capture", return_value=(True, "moved to ideas")) as conv:
        status, _, body = _post(
            "/api/capture/convert",
            {"from": "notes", "to": "ideas", "index": 1, "original": "actually an idea"},
        )
    assert status == 200
    assert json.loads(body)["ok"] is True
    conv.assert_called_once_with("notes", 1, "actually an idea", "ideas")


@pytest.mark.parametrize("payload", [
    {"from": "../../etc/passwd", "to": "ideas", "index": 0, "original": "x"},
    {"from": "notes", "to": "projects/sokrates/projects/nova/ideas.md", "index": 0, "original": "x"},
    {"to": "ideas", "index": 0, "original": "x"},
    {"from": "notes", "index": 0, "original": "x"},
    # Converting a line into the file it is already in is a no-op the page
    # should never send and the server should never carry out -- it would
    # write the copy and then delete the address it had just shifted.
    {"from": "notes", "to": "notes", "index": 0, "original": "x"},
    {"from": "notes", "to": "ideas", "index": 0, "original": ""},
    {"from": "notes", "to": "ideas", "index": 0, "original": 42},
    {"from": "notes", "to": "ideas", "original": "x"},
    {"from": "notes", "to": "ideas", "index": True, "original": "x"},
    {"from": "notes", "to": "ideas", "index": -1, "original": "x"},
    {"from": "notes", "to": "ideas", "index": "0", "original": "x"},
])
def test_a_convert_that_could_address_the_wrong_document_is_rejected(payload):
    with patch.object(nova_site, "convert_capture") as conv:
        status, _, _ = _post("/api/capture/convert", payload)
    assert status == 400
    conv.assert_not_called()


def test_a_convert_whose_address_went_stale_is_a_conflict_rather_than_a_failure():
    with patch.object(nova_site, "convert_capture",
                      return_value=(False, "that capture is no longer in the list")):
        status, _, _ = _post(
            "/api/capture/convert",
            {"from": "notes", "to": "ideas", "index": 0, "original": "gone"},
        )
    assert status == 409


def test_a_half_done_convert_is_502_even_though_its_message_says_no_longer():
    """`_post_amend`'s 409 means "nothing happened, re-read". Here the same
    substring can appear *after* the destination write landed, and answering
    409 would tell the page nothing changed while a copy sits in `dest`.
    Reviewer finding on this change."""
    with patch.object(nova_site, "convert_capture", return_value=(
            False, "copied to ideas, but could not remove it from notes "
                   "(that capture is no longer in the list) — it is in both, "
                   "delete the notes one")):
        status, _, _ = _post(
            "/api/capture/convert",
            {"from": "notes", "to": "ideas", "index": 0, "original": "x"},
        )
    assert status == 502, "a landed destination write must not read as nothing happened"


def test_an_exception_after_the_destination_write_still_drops_both_pages():
    """The write may already have happened when the exception is raised, and a
    cached page would hide the copy that really is there."""
    nova_site.reset_cache()
    nova_site._cache["notes"] = ({"notes": ["stale"]}, "{}", 'W/"x"', 0.0)
    nova_site._cache["board:ideas"] = ({"captures": ["stale"]}, "{}", 'W/"x"', 0.0)
    with patch.object(nova_site, "convert_capture", side_effect=RuntimeError("boom")):
        status, _, _ = _post(
            "/api/capture/convert",
            {"from": "notes", "to": "ideas", "index": 0, "original": "x"},
        )
    assert status == 502
    assert "notes" not in nova_site._cache
    assert "board:ideas" not in nova_site._cache


def test_a_convert_drops_both_pages_it_touched_even_when_it_half_failed():
    """The half-done state -- copied, not removed -- has to leave both pages
    cold. A cached destination would keep the copy invisible while the
    message says it is in both files."""
    nova_site.reset_cache()
    nova_site._cache["notes"] = ({"notes": ["stale"]}, "{}", 'W/"x"', 0.0)
    nova_site._cache["board:ideas"] = ({"captures": ["stale"]}, "{}", 'W/"x"', 0.0)
    with patch.object(nova_site, "convert_capture",
                      return_value=(False, "copied to ideas, but ... it is in both")):
        _post("/api/capture/convert",
              {"from": "notes", "to": "ideas", "index": 0, "original": "x"})
    assert "notes" not in nova_site._cache
    assert "board:ideas" not in nova_site._cache


@pytest.mark.parametrize("path", ["/api/capture", "/api/capture/edit", "/api/capture/delete"])
def test_writing_a_note_drops_the_notes_page_not_a_board_that_never_existed(path):
    """`board:notes` has never existed -- notes are not a board -- and for
    every write but the first this endpoint only ever popped that key. A
    note edited or deleted from the app left `/notes` serving the copy from
    before the write."""
    nova_site.reset_cache()
    nova_site._cache["notes"] = ({"notes": ["stale"]}, "{}", 'W/"x"', 0.0)
    with patch.object(nova_site, "capture", return_value=(True, "ok")), \
            patch.object(nova_site, "amend", return_value=(True, "ok")):
        _post(path, {"target": "notes", "index": 0, "original": "x", "text": "y"})
    assert "notes" not in nova_site._cache, (
        "the notes page would reload to the copy from before the write"
    )


# Parametrized over the dict rather than a literal list: this test was
# `["issues", "ideas"]` and its name said "both", so adding `notes` as a
# third target left a test that still passed, still read as complete, and
# covered two thirds of the endpoint. Derived, it cannot go stale again.
@pytest.mark.parametrize("target", sorted(nova_capture.CAPTURE_TARGETS))
def test_every_target_is_accepted(target):
    with patch.object(nova_site, "capture", return_value=(True, "ok")) as cap:
        status, _, _ = _post("/api/capture", {"target": target, "text": "x"})
    assert status == 200
    assert cap.call_args[0][0] == target


@pytest.mark.parametrize("payload", [
    {"target": "journal", "text": "x"},
    {"target": "../../etc/passwd", "text": "x"},
    {"target": "projects/sokrates/projects/nova/issues.md", "text": "x"},
    {"text": "x"},
    {"target": "issues"},
    {"target": "issues", "text": 42},
])
def test_a_bad_payload_is_rejected_before_the_vault_is_touched(payload):
    """Notably a `target` that is a path: nothing a client sends is ever
    used to address a vault document."""
    with patch.object(nova_site, "capture") as cap:
        status, _, _ = _post("/api/capture", payload)
    assert status == 400
    cap.assert_not_called()


def test_an_oversized_body_is_refused_without_being_read():
    """Content-Length is attacker-controlled and `rfile.read(n)` allocates
    it; this pod's memory limit is 256Mi. The body is deliberately never
    sent -- if the handler read it, the test would hang or mis-parse."""
    oversized = nova_capture.MAX_BODY_BYTES + 1
    request = (
        f"POST /api/capture HTTP/1.1\r\nHost: nova\r\n"
        f"Content-Type: application/json\r\nContent-Length: {oversized}\r\n\r\n"
    ).encode()
    sock = _FakeSocket(request)
    with patch.object(nova_site, "capture") as cap:
        nova_site.NovaSiteHandler(sock, ("127.0.0.1", 50000), _FakeServer())
    status = int(sock.sent.getvalue().split(b" ", 2)[1])
    assert status == 413
    cap.assert_not_called()


def test_a_body_at_the_limit_is_still_accepted():
    """The cap is a memory bound, not an opinion about how much the owner may
    type, so the boundary itself has to pass."""
    text = "x" * (nova_capture.MAX_BODY_BYTES - 200)
    with patch.object(nova_site, "capture", return_value=(True, "ok")) as cap:
        status, _, _ = _post("/api/capture", {"target": "ideas", "text": text})
    assert status == 200
    assert len(cap.call_args[0][1]) == len(text)


def test_a_form_content_type_is_refused():
    """The CSRF property: requiring application/json makes this a
    preflighted request, and the server answers no OPTIONS and sends no
    CORS headers, so a page on another origin cannot post here."""
    with patch.object(nova_site, "capture") as cap:
        status, _, _ = _post(
            "/api/capture", {"target": "issues", "text": "x"},
            content_type="application/x-www-form-urlencoded",
        )
    assert status == 415
    cap.assert_not_called()


def test_the_site_sends_no_cors_headers():
    """Without these the preflight above cannot succeed. If a future change
    adds them, this write endpoint stops being same-origin-only."""
    for path in ["/", "/api/journal"]:
        _, head, _ = _get(path)
        assert "access-control-allow-origin" not in head.lower(), path


def test_malformed_json_is_a_400_not_a_500():
    sock = _FakeSocket(
        b"POST /api/capture HTTP/1.1\r\nHost: nova\r\n"
        b"Content-Type: application/json\r\nContent-Length: 5\r\n\r\n{not!"
    )
    nova_site.NovaSiteHandler(sock, ("127.0.0.1", 50000), _FakeServer())
    assert int(sock.sent.getvalue().split(b" ", 2)[1]) == 400


def test_a_failed_capture_is_reported_as_502_so_the_client_keeps_the_text():
    """app.js only clears the box on ok:true -- a failure that looked like a
    success would silently eat the thought the box exists to catch."""
    with patch.object(nova_site, "capture", return_value=(False, "could not write")):
        status, _, body = _post("/api/capture", {"target": "issues", "text": "x"})
    assert status == 502
    assert json.loads(body)["ok"] is False


def test_a_capture_that_raises_is_a_502_not_a_traceback():
    with patch.object(nova_site, "capture", side_effect=RuntimeError("couchdb down")):
        status, _, body = _post("/api/capture", {"target": "issues", "text": "x"})
    assert status == 502
    assert "couchdb down" in json.loads(body)["error"]


def test_every_capture_attempt_is_audited_including_the_failures():
    with patch.object(nova_site, "capture", return_value=(False, "could not write")), \
            patch.object(nova_site, "audit") as audited:
        _post("/api/capture", {"target": "issues", "text": "x"})
    assert audited.call_count == 1
    assert audited.call_args.kwargs["is_error"] is True


def test_start_nova_site_binds_and_serves_the_real_handler():
    """Drives the actual entry point main() calls. Eight QuotaWatcher tests
    in the bridge all called `_run()` directly and left `start()` covered by
    nothing; this is the same seam, so it gets the real function."""
    with patch.object(nova_site, "NOVA_PORT", 0), \
            patch.object(nova_site, "warm_cache"):
        server = nova_site.start_nova_site()
    try:
        assert server.RequestHandlerClass is nova_site.NovaSiteHandler
        assert server.server_address[1] != 0
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def site_main():
    """`agora_runner.nova_site_main`, with its module flag and this
    process's signal handlers restored afterwards. main() installs real
    handlers and the tests below deliver a real SIGTERM, so leaking either
    would break whatever test ran next."""
    module = importlib.import_module("agora_runner.nova_site_main")
    previous_flag = module._shutdown_requested
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    module._shutdown_requested = False
    try:
        yield module
    finally:
        module._shutdown_requested = previous_flag
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def test_site_main_serves_until_sigterm_then_releases_the_port(site_main):
    """The real `main()`, the real server, a real SIGTERM.

    Two things have to hold and they fail independently. main() must
    *return* rather than keep sleeping -- that is the drain -- and it must
    close the listening socket on the way out. A ThreadingHTTPServer that
    is shut down but not closed keeps 8083 bound, so the next process to
    start would fail to bind while the pod's readiness probe was still
    being answered by a server nobody is driving.

    The signal is delivered from inside the sleep the loop is actually
    sitting in, which is where it arrives in the pod too.
    """
    served = []

    def capture_server():
        with patch.object(nova_site, "NOVA_PORT", 0), \
                patch.object(nova_site, "warm_cache"):
            server = nova_site.start_nova_site()
        served.append(server)
        return server

    def sleep_then_sigterm(_seconds):
        os.kill(os.getpid(), signal.SIGTERM)  # ArgoCD rolls the nova-site pod

    with patch.object(site_main, "start_nova_site", side_effect=capture_server), \
            patch.object(site_main, "time") as clock, \
            patch.object(site_main, "log", lambda *a, **k: None):
        clock.sleep.side_effect = sleep_then_sigterm
        site_main.main()

    assert site_main.shutdown_requested() is True
    assert clock.sleep.call_count == 1, "main slept again after SIGTERM"
    assert served[0].socket.fileno() == -1, "the listening socket was left open"


def test_site_main_closes_the_port_even_if_the_loop_raises(site_main):
    """The `finally` is load-bearing, not decoration. If the sleep loop
    dies for any reason the pod is going away regardless, and a half-shut
    server is the one state that leaves the port bound with nothing
    serving it."""
    served = []

    def capture_server():
        with patch.object(nova_site, "NOVA_PORT", 0), \
                patch.object(nova_site, "warm_cache"):
            server = nova_site.start_nova_site()
        served.append(server)
        return server

    with patch.object(site_main, "start_nova_site", side_effect=capture_server), \
            patch.object(site_main, "time") as clock, \
            patch.object(site_main, "log", lambda *a, **k: None):
        clock.sleep.side_effect = KeyboardInterrupt("kubelet gave up waiting")
        with pytest.raises(KeyboardInterrupt):
            site_main.main()

    assert served[0].socket.fileno() == -1


def test_the_runner_process_no_longer_serves_the_site():
    """The site moved to its own Deployment on 2026-08-09. If the runner
    started it as well, both pods would serve 8083 and the Service would
    round-robin between a live site and one that disappears for the length
    of every cycle -- which is worse than either alone, and would look
    like the site working intermittently rather than like a bug.

    Asserting on the module rather than on a mock on purpose: this is a
    claim about something *not* happening, and a test that patches
    `start_nova_site` to prove it isn't called would pass just as happily
    if the import were still there and the call moved elsewhere.

    Over the parsed tree rather than over the text, for the same reason
    Cycle 51's `innerHTML` test strips comments first: main.py's docstring
    explains at length why the site is *not* started here, and a substring
    search cannot tell that sentence apart from the thing it forbids.
    """
    main_module = importlib.import_module("agora_runner.main")
    assert not hasattr(main_module, "start_nova_site")
    tree = ast.parse(inspect.getsource(main_module))
    imported = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "agora_runner.nova_site" not in imported
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "start_nova_site" not in called


def test_the_site_entrypoint_script_reaches_site_main():
    """`run_nova_site.py` is what the nova-site Deployment's `command`
    runs, and it is the one file in this chain that no other test imports.
    A typo in it is a CrashLoopBackOff that the whole suite goes green
    through."""
    entrypoint = importlib.import_module("run_nova_site")
    site_main_module = importlib.import_module("agora_runner.nova_site_main")
    assert entrypoint.main is site_main_module.main


# --- Cycle 56: the emoji the owner asked for, and the collapsed card ---


def _entry(body, title=""):
    return {"body": body, "title": title}


def test_every_entry_gets_an_emoji():
    """app.js reads `entry.emoji` unconditionally. An entry matching no
    topic at all must still carry the fallback rather than `undefined`."""
    entries = assign_emoji([_entry("Nothing in here matches any topic whatsoever.")])
    assert entries[0]["emoji"] == "\U0001f527"


def test_a_word_in_every_entry_loses_to_a_word_in_one():
    """The whole reason scoring is corpus-relative rather than raw counts.

    Every cycle writes a journal and reads the digest, so those words are
    in all 57 live entries and discriminate between none of them -- scored
    naively they won 18 outright, including entries about outages. Here
    "journal" appears four times in the entry that is really about
    heartbeats, and once in every other entry; "heartbeat" appears twice,
    in one. Rarity has to beat frequency or the feed is a wall of 📓.
    """
    corpus = [
        _entry("journal journal journal journal. heartbeat cadence heartbeat."),
        _entry("journal digest vault, a cycle about the journal."),
        _entry("journal digest vault, another about the digest."),
        _entry("journal digest vault, a third about the vault."),
    ]
    assigned = assign_emoji(corpus)
    assert assigned[0]["emoji"] == "\U0001f493"
    assert assigned[1]["emoji"] == "\U0001f4d3"


def test_an_outage_beats_the_infrastructure_it_happened_in():
    """Cycles 53 and 54 were both real outages whose bodies are necessarily
    full of pods, manifests and ArgoCD -- and on plain scoring the
    infrastructure vocabulary won, labelling two hours of downtime as
    routine wrench work. Severity overrides topic."""
    entry = _entry(
        "I broke the bridge for two hours and no cycle ran.\n\n"
        "kubectl kubectl manifest manifest argocd argocd deploy deploy "
        "namespace namespace pod pod kubernetes limitrange"
    )
    assert assign_emoji([entry])[0]["emoji"] == "\U0001f6a8"


def test_an_outage_only_narrated_is_not_this_cycles_outage():
    """The counterweight, and the reason the override reads the opening
    paragraph rather than the body. Every entry in this journal narrates
    the previous cycle's failures, so a body-wide match makes 17 of 57
    cycles look like incidents when 5 were."""
    entry = _entry(
        "Heartbeats can be scheduled on a cron expression now.\n\n"
        "Cycle 53 was OOMKilled and Cycle 54 spent the whole outage on it."
    )
    assert assign_emoji([entry])[0]["emoji"] != "\U0001f6a8"


def test_the_payload_carries_the_emoji_the_client_reads():
    """journal_payload rebuilds each entry as a new dict; the emoji has to
    survive that or app.js renders a card with no icon and no error."""
    with patch.object(nova_sources, "vault_read_path", return_value=_fixture("journal_sample.md")):
        payload = nova_site.journal_payload()
    assert payload["entries"]
    assert all(entry.get("emoji") for entry in payload["entries"])


# The marker that lets one line out of the ban below.
NOT_PROSE = "not-prose"


def truncations_in(source):
    """Lines of JavaScript that cut a string and did not say why.

    The rule this serves is that a journal card's prose is hidden by CSS
    and never cut client-side -- a server-side or client-side truncation
    puts the text out of reach of find-in-page, and cutting it mid-sentence
    is what the owner reported on 2026-08-09.

    It stays a blunt, whole-file ban on `slice(0,` and `substring(` on
    purpose. Cycle 323 first tried narrowing it to receivers that *read*
    like entry text, and the reviewer took it apart in four lines: a
    truncation survives that check by being written as
    `entry.body.trim().slice(0, 500)`, or behind a ternary, or assigned to
    a local called `lead` first, or wrapped in a two-line `clamp(s)`
    helper. Every one of those cuts the prose and none of them is a bare
    keyword-named receiver. A guard that catches only the laziest spelling
    of a bug is worse than none, because it reads as cover.

    So completeness is kept and the false positive gets an explicit,
    visible escape hatch instead: a line may carry the token if it also
    carries a `not-prose` comment. That is a sentence somebody has to
    write on purpose and a reviewer sees in the diff, which is what makes
    it different from widening the pattern until the red goes away. The
    one use today is `buildPrioPicker`'s glyph split, which turned `main`
    red on 2026-08-22 over `label.slice(0, sp)` -- a priority chip cutting
    `🟠 High` down to `🟠`, no reader's sentence involved.
    """
    # Comments become blank lines rather than vanishing, so a line index
    # here is the same line index in the file the caller is reading, and
    # the marker itself is never mistaken for code.
    stripped = re.sub(
        r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), source, flags=re.DOTALL
    )
    found = []
    for number, (code_line, raw) in enumerate(
        zip(stripped.split("\n"), source.split("\n")), start=1
    ):
        if "slice(0," not in code_line.replace(" ", "") and "substring(" not in code_line:
            continue
        if NOT_PROSE in raw:
            continue
        found.append(f"{number}: {raw.strip()}")
    return found


def test_the_truncation_guard_can_actually_fail():
    """The guard above is a regex over a file that happens to contain no
    matches, so nothing in `test_a_collapsed_card_hides_the_body_without_
    dropping_it` would notice if the pattern itself were wrong -- it would
    pass green forever. The reviewer raised that on runner#293 and it is
    the same shape as the `lint_entry | tail` failure: a guard reporting
    itself working while guarding nothing.

    Each of these is a real way to cut a card's prose, and four of the
    five defeated the narrower version this replaced.
    """
    assert truncations_in("var x = entry.body.slice(0, 200);")
    assert truncations_in("var x = entry.body.trim().slice(0, 500);")
    assert truncations_in("var x = (d ? d.text : firstParagraph(e.blocks)).slice(0, 200);")
    assert truncations_in("var lead = firstParagraph(entry.blocks); var y = lead.slice(0, 240);")
    assert truncations_in("function clamp(s) { return s.length > n ? s.slice(0, n) : s; }")
    assert truncations_in("var x = brief.substring(0, 80);")
    # Not a truncation, and not a hole in the pattern: the exemption is a
    # comment on the line, which a reviewer reads.
    assert truncations_in("return label.slice(0, sp); // not-prose: a priority glyph") == []
    assert truncations_in("var x = list.map(f);") == []
    # A commented-out truncation is not a truncation, and a marker inside a
    # block comment does not license the line after it.
    assert truncations_in("/* entry.body.slice(0, 5) */") == []
    assert truncations_in("/* not-prose */\nvar x = entry.body.slice(0, 5);")


def test_a_collapsed_card_hides_the_body_without_dropping_it():
    """The owner asked for cards that collapse to 2-3 lines, then for a drawer
    within a drawer. Every level is hidden by a class and none of them is cut
    -- all the text stays in the DOM. A server-side truncation would have been
    the easy version and would have put the prose out of reach of find-in-page.

    The line clamp this used to assert is deliberately gone. The brief now
    arrives already cut on a sentence boundary, so a clamp could only break it
    again in the middle, which is the thing the owner reported on 2026-08-09."""
    css = open(
        os.path.join(os.path.dirname(nova_site.PUBLIC_DIR), "nova_public", "style.css"),
        encoding="utf-8",
    ).read()
    # `.entry-parts`, not `.entry-body`: a cycle that wrote twice has two
    # bodies inside the one drawer, and one of them cannot be what opens.
    assert ".entry.is-expanded.is-reading .entry-parts { display: block; }" in css
    assert ".entry .entry-parts .entry-body { display: block; }" in css
    assert ".entry.is-expanded .journal-toggle" in css
    # The clamp survives on exactly one selector -- the unsplit fallback for a
    # payload sw.js cached before this shipped -- and nowhere else.
    assert re.findall(r"(?m)^([^\n{]*)\{[^}]*line-clamp", css) == [
        ".entry.is-collapsed .entry-brief.is-unsplit ",
    ]

    source = open(
        os.path.join(os.path.dirname(nova_site.PUBLIC_DIR), "nova_public", "app.js"),
        encoding="utf-8",
    ).read()
    assert truncations_in(source) == [], (
        "app.js truncates a string; if it is not entry prose, say so with "
        f"a `{NOT_PROSE}` comment on the line: {truncations_in(source)}"
    )


# --- The `Needs Edvard` box, and the emphasis that kept it on screen -------  (not-prose: quoting a literal)


@pytest.mark.parametrize(
    "text",
    ["**Nothing.**", "*Nothing*", "__none__", "`nothing`", "**None**", "Nothing.", "  ", ""],
)
def test_a_bolded_nothing_is_still_nothing(text):
    """The owner, issues.md 2026-08-09: "The 'needs the owner' box should not show
    when nothing is expected."

    The section was compared literally, so `Nothing.` was empty and
    `**Nothing.**` was a live claim on his attention. Every cycle writes the
    bold one -- it is the house style for that section, and the live digest
    has read `**Nothing.**` continuously -- so the box had never once been
    correctly hidden since it shipped."""
    assert is_empty_needs(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "**Decide whether to buy a node.**",
        "Nothing has been decided about the node yet.",
        "- Nothing works. Please look.",
        "none of the three options is obviously right",
    ],
)
def test_a_real_ask_is_not_mistaken_for_an_empty_one(text):
    """The stripping must not swallow a section that happens to start with
    the word. Only a section that is *nothing but* some spelling of
    "nothing" counts as empty."""
    assert is_empty_needs(text) is False


# --- PR references become links -------------------------------------------


@pytest.mark.parametrize(
    "field, expected",
    [
        ("#28", [("#28", "agora-persona-runner/pull/28")]),
        ("agora#45", [("agora#45", "agora/pull/45")]),
        ("bridge#9", [("bridge#9", "agora-claude-bridge/pull/9")]),
        ("platform-config#485", [("platform-config#485", "platform-config/pull/485")]),
        ("runner-config#6", [("runner-config#6", "agora-persona-runner-config/pull/6")]),
        ("bridge-config#8", [("bridge-config#8", "agora-claude-bridge-config/pull/8")]),
        # Cycle 6's bare `config`, which meant the runner's config repo.
        ("config#2", [("config#2", "agora-persona-runner-config/pull/2")]),
        # A parenthetical naming the repo instead of a prefix.
        ("#38 (runner)", [("#38 (runner)", "agora-persona-runner/pull/38")]),
        ("#40 (SokratesAI/agora)", [("#40 (SokratesAI/agora)", "agora/pull/40")]),
    ],
)
def test_a_reference_resolves_to_the_repo_it_names(field, expected):
    links = [(s["text"], s["url"]) for s in parse_pr_refs(field) if s["kind"] == "link"]
    assert links == [(text, "https://github.com/SokratesAI/" + path) for text, path in expected]


@pytest.mark.parametrize(
    "field, count",
    [
        ("runner#58, runner-config#6, platform-config#490", 3),
        ("#38 (runner) + bridge#11", 2),
        ("#32, #31, config#2", 3),
        ("#49, #50 (both merged)", 2),
        ("none", 0),
        ("none (status note)", 0),
        ("", 0),
    ],
)
def test_every_reference_in_a_field_is_found_and_nothing_else_is(field, count):
    assert sum(1 for s in parse_pr_refs(field) if s["kind"] == "link") == count


@pytest.mark.parametrize(
    "field",
    ["#51 (merged)", "#49 (open)", "bridge#19 (merged)"],
)
def test_a_parenthetical_that_names_no_repo_is_left_alone(field):
    """`(merged)` and `(open)` sit in the same position as `(runner)` and
    must not be swallowed into the link text, or the card stops saying what
    happened to the PR."""
    spans = parse_pr_refs(field)
    assert [s["text"] for s in spans if s["kind"] == "link"] == [field.split(" ")[0]]
    assert "".join(s["text"] for s in spans) == field


def test_an_unrecognised_prefix_becomes_no_link_at_all():
    """A confidently wrong link is worse than plain text: it looks
    authoritative and goes to another project's PR of the same number."""
    spans = parse_pr_refs("someothersystem#12")
    assert not [s for s in spans if s["kind"] == "link"]
    assert "".join(s["text"] for s in spans) == "someothersystem#12"


def test_no_pr_field_in_the_live_journal_loses_a_character(journal_md):
    """The spans must reassemble the field exactly. This is the invariant
    that makes "leave it as is" (the owner's words) checkable rather than
    asserted -- linkifying is allowed to add structure and never to edit."""
    fields = {e["pr"] for e in parse_journal_file(journal_md) if e["pr"]}
    assert fields
    for field in fields:
        assert "".join(s["text"] for s in parse_pr_refs(field)) == field


def test_the_payload_carries_the_pr_spans_the_client_reads():
    with patch.object(nova_sources, "vault_read_path", return_value=_fixture("journal_sample.md")):
        payload = nova_site.journal_payload()
    assert payload["entries"]
    assert all("prSpans" in entry for entry in payload["entries"])


# --- The `Board:` field becomes links into the owner's own boards -------------


@pytest.mark.parametrize(
    "field, expected",
    [
        ("idea #68", [("idea #68", "/ideas#68")]),
        ("issue #71", [("issue #71", "/issues#71")]),
        ("Idea #68", [("Idea #68", "/ideas#68")]),
        ("ideas #68", [("ideas #68", "/ideas#68")]),
        ("issue #71, idea #62", [("issue #71", "/issues#71"), ("idea #62", "/ideas#62")]),
        ("idea #68 (the linking half)", [("idea #68", "/ideas#68")]),
    ],
)
def test_a_board_reference_resolves_to_the_page_that_holds_it(field, expected):
    links = [(s["text"], s["url"]) for s in parse_board_refs(field) if s["kind"] == "link"]
    assert links == expected


@pytest.mark.parametrize("field", ["#68", "none", "", "68", "idea 68", "PR #68"])
def test_a_board_field_with_no_word_and_number_makes_no_link(field):
    """A bare `#68` is the one shape that must stay plain text. In the `PR:`
    field a bare number has exactly one meaning; here it could be either
    board, and the two are different pages -- so guessing sends the owner to a
    real write-up that is not the one the cycle worked on."""
    spans = parse_board_refs(field)
    assert not [s for s in spans if s["kind"] == "link"]
    assert "".join(s["text"] for s in spans) == field


@pytest.mark.parametrize(
    "field",
    ["idea #68", "issue #71, idea #62", "idea #68 (the linking half)", "#68", "none"],
)
def test_no_board_field_loses_a_character(field):
    """Same invariant as the PR field: linkifying adds structure and never
    edits. Without this the check above passes on a parser that drops the
    text it could not place."""
    assert "".join(s["text"] for s in parse_board_refs(field)) == field


@pytest.mark.parametrize(
    "footer, pr, board, outcome",
    [
        ("PR: #160 | Board: idea #68 | Outcome: merged", "#160", "idea #68", "merged"),
        ("PR: #160 | Outcome: merged", "#160", "", "merged"),
        ("PR: none | Board: issue #71 | Outcome: shipped", "none", "issue #71", "shipped"),
        # The field is free text like the two beside it.
        (
            "PR: #1, bridge#2 | Board: idea #68, issue #71 | Outcome: merged",
            "#1, bridge#2",
            "idea #68, issue #71",
            "merged",
        ),
    ],
)
def test_the_footer_reads_three_fields_and_two_of_them_are_required(
    footer, pr, board, outcome
):
    entry = parse_journal(f"### 2026-08-13 22:00 (Oslo) — Cycle 176\n\nBody.\n\n---\n{footer}")[0]
    assert (entry["pr"], entry["board"], entry["outcome"]) == (pr, board, outcome)


def test_a_board_field_survives_a_footer_the_site_has_to_move():
    """`stray_footer` repairs the three live entries that put the footer in
    the wrong place. It carries four fields now, and a repaired entry that
    silently dropped its board reference would be the harder bug to see --
    the card renders, just without the link."""
    body = "**PR: #160 | Board: idea #68 | Outcome: merged**\n\nThe cycle went like this."
    entry = parse_journal(f"### 2026-08-13 22:00 (Oslo) — Cycle 176\n\n{body}")[0]
    assert (entry["pr"], entry["board"], entry["outcome"]) == ("#160", "idea #68", "merged")
    assert "PR:" not in entry["body"]


def test_a_pipe_in_the_pr_field_is_not_mistaken_for_a_board_field():
    """The board group sits between two fields that were already there, so
    the risk it introduces is to the ones it did not change. `Outcome:` is
    what closes the footer, and nothing before it may claim to be a board
    unless it says so."""
    entry = parse_journal(
        "### 2026-08-13 22:00 (Oslo) — Cycle 176\n\nBody.\n\n---\n"
        "PR: #160 | see also #161 | Outcome: merged"
    )[0]
    assert entry["pr"] == "#160 | see also #161"
    assert entry["board"] == ""


def test_the_payload_carries_the_board_spans_the_client_reads():
    with patch.object(nova_sources, "vault_read_path", return_value=_fixture("journal_sample.md")):
        payload = nova_site.journal_payload()
    assert payload["entries"]
    assert all("board" in entry and "boardSpans" in entry for entry in payload["entries"])


# --- The contract with the browser tests ----------------------------------


def test_the_browser_fixture_is_what_the_server_would_send():
    """tests/browser/ runs under node and cannot import any of this, so the
    two halves meet at a committed JSON file. Without this test that file is
    a hand-written mock that is free to drift away from the server it claims
    to imitate -- which is the whole reason those tests would stop meaning
    anything, quietly. Regenerate with `python3 tests/browser/regen.py`."""
    import json

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser"))
    import regen

    with open(regen.PAYLOAD_PATH, encoding="utf-8") as handle:
        committed = json.load(handle)
    assert regen.build_payload() == committed, (
        "tests/browser/fixtures/payload.json no longer matches what nova_site "
        "would send. Re-run: python3 tests/browser/regen.py"
    )


def _css_rule(selector):
    """The declarations of one CSS rule, by exact selector.

    Substring-matching the whole stylesheet would pass on the same property
    set in some other rule, which for a colour is exactly the mistake worth
    not making."""
    css = open(os.path.join(nova_site.PUBLIC_DIR, "style.css"), encoding="utf-8").read()
    match = re.search(r"(?m)^" + re.escape(selector) + r"[ \t]*\{([^}]*)\}", css)
    assert match, f"no rule for {selector!r} in style.css"
    return match.group(1)


def test_the_digest_summary_is_the_same_colour_as_the_prose_it_summarises():
    """The owner, issues.md 2026-08-09: "the Digest is hard to read with grey
    against the blue background. White is better like the actual journal."

    Worth a test rather than a look, because this is the one change of the
    four that a green suite said nothing about: reverting it left all 1049
    other tests passing. A styling fix nothing pins is a styling fix the
    next refactor silently undoes -- and the element it was written for has
    since been renamed to `.entry-brief`, which is exactly the kind of quiet
    undoing it exists to catch."""
    declarations = _css_rule(".entry-brief")
    assert "color: var(--text)" in declarations
    assert "var(--dim)" not in declarations


def test_a_digest_line_carries_its_bold_as_spans_not_asterisks():
    """Nearly every digest line opens with a bolded sentence. The card
    rendered `text` verbatim, so it was the only text on the page showing
    its own markup -- and it is the same line the owner called hard to read.
    Found by rendering the live files rather than the fixtures, which do
    not happen to contain a bold digest line.

    Checked across the two drawers together, because that is now the whole
    line: the third `spans` field carrying the same text again went with
    the digest window, and this guarantee has to survive without it."""
    lines = parse_digest(_fixture("digest_two_entries.md"))["lines"]
    assert lines
    bolded = [line for line in lines if "**" in line["text"]]
    assert bolded, "the fixture has no bold digest line to check"
    for line in bolded:
        spans = line["briefSpans"] + line["restSpans"]
        kinds = {span["kind"] for span in spans}
        assert "strong" in kinds
        assert all("**" not in span["text"] for span in spans)
        brief = "".join(s["text"] for s in line["briefSpans"])
        rest = "".join(s["text"] for s in line["restSpans"])
        # The split between the drawers eats exactly the one space it cut
        # on, and nothing else -- spelled out rather than normalised away,
        # because "no character is lost" is the point of the assertion.
        rebuilt = brief + " " + rest if rest else brief
        assert rebuilt == line["text"].replace("**", "")


def test_two_digest_lines_with_no_blank_line_between_them_are_two_cards():
    """A missing blank line cost the owner a whole card without anything going
    red. The live file had Cycle 66 and Cycle 65 separated by a single
    newline, so the digest parsed 21 cards from 22 lines: Cycle 65 vanished
    and Cycle 66's card ended on Cycle 65's closing sentence. The card
    boundary is a `**Cycle N** (` at the start of a line, not a blank."""
    glued = (
        "## Digest\n\n"
        "**Cycle 66** (2026-08-09 23:22) — What cycle 66 did.\n"
        "**Cycle 65** (2026-08-09 22:42) — What cycle 65 did.\n"
    )
    lines = parse_digest(glued)["lines"]
    assert [line["cycle"] for line in lines] == [66, 65]
    assert lines[0]["text"] == "What cycle 66 did."
    assert lines[1]["text"] == "What cycle 65 did."


def test_the_digest_section_prose_is_still_split_on_blank_lines():
    """The lookahead is an addition, not a replacement -- anything in the
    section that is not a digest line has to keep separating normally, or
    a stray paragraph would glue itself onto the card above it."""
    mixed = (
        "## Digest\n\n"
        "A paragraph that is not a digest line.\n\n"
        "**Cycle 66** (2026-08-09 23:22) — What cycle 66 did.\n\n"
        "Another paragraph that is not a digest line.\n"
    )
    lines = parse_digest(mixed)["lines"]
    assert [line["cycle"] for line in lines] == [66]
    assert lines[0]["text"] == "What cycle 66 did."


def test_a_cycle_line_glued_under_prose_still_gets_its_own_card():
    """The case the lookahead actually exists for, and the worse half of
    the bug: with prose above it and no blank line, the whole block used
    to fail `_DIGEST_LINE_RE` outright, so the card was not merged into
    its neighbour -- it was dropped without trace. Raised by the reviewer
    subagent against the first version of this change, which pinned the
    blank-line half and left this one untested."""
    glued_to_prose = (
        "## Digest\n\n"
        "A note somebody left at the top of the section.\n"
        "**Cycle 66** (2026-08-09 23:22) — What cycle 66 did.\n"
    )
    lines = parse_digest(glued_to_prose)["lines"]
    assert [line["cycle"] for line in lines] == [66]
    assert lines[0]["text"] == "What cycle 66 did."


# --- The brief, and the drawer within a drawer ----------------------------
#
# the owner, issues.md 2026-08-09: "I need a 2-3 line short precise Digest for
# each cycle as a title for each journey card ... As short as possible, max 3
# sentences ... Then, when a journey card is opened, the Digest is revealed.
# Below that, a 'read the full journal' button to expand the full journal ...
# So its a drawer within a drawer."


def test_a_brief_ends_on_a_sentence_never_mid_word():
    """The whole point of the change. The card used to clamp the digest line
    in CSS, which cuts wherever the third line box happens to end -- so every
    card trailed off mid-sentence, which is what he was reporting."""
    text = (
        "The site stopped going down every time I run. It has its own pod now, "
        "which matters because the capture box was dead at exactly the moment "
        "you would have been reading about a cycle in progress. Three PRs. "
        "The hard part was already done by an earlier cycle that got killed."
    )
    brief, rest = split_brief(text)
    assert brief.endswith(".")
    assert text.startswith(brief)
    # Without this the test survives a mutant that never splits at all: the
    # whole summary also "ends on a sentence" and is also a prefix of itself.
    # A mutation run on 2026-08-09 caught exactly that.
    assert len(brief) < len(text)
    assert rest


def test_brief_and_rest_reconstruct_the_whole_summary():
    """Nothing is thrown away -- the remainder is the next drawer down, not a
    truncation. Asserted over the shapes the live files actually contain."""
    for text in [
        "**One bold headline sentence.** Then the long explanation follows here.",
        "A plain first sentence. A second one. A third one. A fourth one.",
        "Only one sentence and no more.",
        "**Bold that does not end the sentence** — and then it continues. More.",
    ]:
        brief, rest = split_brief(text)
        assert ((brief + " " + rest).strip() if rest else brief) == text


def test_a_bold_opening_sentence_is_the_whole_brief():
    """The house style for a digest line is a bolded opening sentence saying
    what changed for the owner -- all 9 live lines have one. That sentence was
    written to be the headline, so pulling a second sentence in after it
    whenever the headline was short is the opposite of "as short as possible"."""
    text = "**Short headline.** A second sentence that the budget would otherwise have room for."
    brief, rest = split_brief(text)
    assert brief == "**Short headline.**"
    assert rest.startswith("A second sentence")


def test_a_bold_label_is_not_the_report_cards_whole_title():
    """The owner, issues #86: "the 8cycle reports have just the word tl;dr as
    title". Report 242's first paragraph really is shaped like this, and
    `split_brief` alone returns `**TL;DR.**` and nothing else."""
    text = (
        "**TL;DR.** These eight hours went on two things. Your boards now say "
        "what has actually shipped."
    )
    assert split_brief(text)[0] == "**TL;DR.**"
    brief, _ = split_brief(strip_brief_label(text))
    assert brief.startswith("These eight hours went on two things.")
    assert "TL;DR" not in brief


def test_a_bold_headline_is_not_mistaken_for_a_label():
    """The discriminator, from both sides. A headline is a sentence and holds
    a space; a label does not. Without the second assertion this passes for a
    `strip_brief_label` that strips nothing at all."""
    headline = "**Short headline.** A second sentence the budget would have room for."
    assert strip_brief_label(headline) == headline
    assert strip_brief_label("**TL;DR.** And then the summary.") == "And then the summary."


def test_a_paragraph_that_is_only_a_label_keeps_it():
    """A brief that is empty is worse than a brief that is a label -- the same
    reason the budget below never drops a long first sentence."""
    assert strip_brief_label("**TL;DR.**") == "**TL;DR.**"


def test_a_brief_takes_at_most_three_sentences():
    text = " ".join(f"Sentence number {n}." for n in range(1, 8))
    brief, rest = split_brief(text)
    assert brief == "Sentence number 1. Sentence number 2. Sentence number 3."
    assert rest == "Sentence number 4. Sentence number 5. Sentence number 6. Sentence number 7."


def test_a_long_first_sentence_is_kept_whole_rather_than_dropped():
    """The budget never produces an empty brief. Cycle 42's entry opens with a
    single 300-character sentence; a card showing nothing would be worse than
    a card showing a long line, and there is nowhere shorter to cut it that is
    still a sentence."""
    long_sentence = "A single sentence of " + "considerable length " * 20 + "indeed."
    brief, rest = split_brief(long_sentence + " A short one.")
    assert brief == long_sentence
    assert rest == "A short one."


def test_a_full_stop_inside_backticks_does_not_end_a_sentence():
    """The journal quotes shell and paths constantly -- 591 inline code spans
    across the live file, and `vault_tool.py get 'a.md'` holds two full stops
    that end nothing."""
    text = "Run `vault_tool.py get 'x.md'` before anything else. Then read it."
    assert split_sentences(text) == [
        "Run `vault_tool.py get 'x.md'` before anything else.",
        "Then read it.",
    ]


def test_a_sentence_ends_after_its_closing_emphasis_not_before_it():
    """`...plainly.** Every cycle` must break after the `**`. Breaking between
    the full stop and it would split one bold span across both drawers and
    leave the markers unbalanced, so the brief would render a stray `**`."""
    sentences = split_sentences("**Done at last.** And here is why it took so long.")
    assert sentences[0] == "**Done at last.**"
    assert all(part.count("**") % 2 == 0 for part in sentences)


def test_every_digest_line_carries_both_drawers():
    lines = parse_digest(_fixture("digest_two_entries.md"))["lines"]
    assert lines
    for line in lines:
        rendered = "".join(s["text"] for s in line["briefSpans"])
        assert rendered, "every digest line needs a brief"
        assert "**" not in rendered, "the brief renders its bold as spans"
        joined = rendered + "".join(s["text"] for s in line["restSpans"])
        # Both drawers together are the whole line. Compared with the inline
        # markers gone, because `render_inline` lifts `**` and backticks into
        # span kinds rather than leaving them in the text.
        flat = re.sub(r"[*`]", "", line["text"])
        assert joined.replace(" ", "") == flat.replace(" ", "")


def test_every_entry_briefs_itself_for_the_cycles_with_no_digest_line():
    """The digest is rewritten every cycle and its older lines are dropped, so
    55 of the 68 live entries have none. Without an entry-level brief that is
    most of the feed collapsing to a row of dates, not a rare fallback."""
    entries = parse_journal_file(_fixture("journal_two_entries.md"))
    assert entries
    for entry in entries:
        assert entry["briefSpans"], "every entry needs a brief of its own"
        rendered = "".join(s["text"] for s in entry["briefSpans"])
        assert "**" not in rendered
        assert len(rendered) <= 700  # a headline, not a paragraph


# --- comments on a cycle (ideas.md #44) -----------------------------------
#
# The storage half is in test_nova_comments.py; what a request may do is
# here. The boundary being defended is the one in the module docstring:
# nothing a client sends addresses a vault document, and every bad request
# is answered before the vault is touched at all.


def test_the_comments_endpoint_groups_by_cycle():
    stored = "## New\n\n### Cycle 63 · 2026-08-09 22:40\n\nkeep it up\n\n## Acknowledged\n"
    with patch.object(nova_sources, "vault_read_path", return_value=stored):
        status, head, body = _get("/api/comments")
    assert status == 200
    assert "application/json" in head
    payload = json.loads(body)
    # String keys, because the client looks them up as `String(entry.cycle)`.
    assert list(payload["byCycle"]) == ["63"]
    assert payload["byCycle"]["63"][0]["text"] == "keep it up"


def test_a_comment_reaches_the_vault_with_the_cycle_it_names():
    with patch.object(nova_site, "add_comment", return_value=(True, "commented on cycle 63")) as add, \
            patch.object(nova_site, "enqueue_reply"), \
            patch.object(nova_site, "audit"):
        status, _, body = _post("/api/comment", {"cycle": 63, "text": "keep it up"})
    assert status == 200
    assert json.loads(body)["ok"] is True
    cycle, text, stamp = add.call_args[0]
    assert (cycle, text) == (63, "keep it up")
    # The stamp is the comment's identity, not decoration: the reply worker
    # finds the comment again by it. See the next test.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", stamp)


def test_a_comment_is_audited_with_what_was_typed():
    with patch.object(nova_site, "add_comment", return_value=(True, "ok")), \
            patch.object(nova_site, "audit") as audit_call:
        _post("/api/comment", {"cycle": 63, "text": "keep it up"})
    assert audit_call.called
    assert audit_call.call_args[1]["after"] == "keep it up"
    assert audit_call.call_args[1]["is_error"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"cycle": 63},                        # no text at all
        {"cycle": 63, "text": 12},            # text that is not a string
        {"cycle": 63, "text": "   \n  "},     # nothing but whitespace
        {"text": "keep it up"},               # no cycle
        {"cycle": "sixty-three", "text": "x"},
        {"cycle": None, "text": "x"},
        {"cycle": -1, "text": "x"},
        {"cycle": {"n": 63}, "text": "x"},
    ],
)
def test_a_malformed_comment_is_400_and_never_touches_the_vault(payload):
    """400 rather than 502: these are the client asking for something
    wrong, not the vault failing, and the difference is what tells the owner
    whether retrying is worth anything."""
    with patch.object(nova_site, "add_comment") as add:
        status, _, _ = _post("/api/comment", payload)
    assert status == 400
    assert not add.called


def test_true_is_not_a_cycle_number():
    """`True` is an int in Python, so an unguarded int() would file a
    comment against cycle 1 -- a real entry, silently the wrong one."""
    with patch.object(nova_site, "add_comment") as add:
        status, _, _ = _post("/api/comment", {"cycle": True, "text": "x"})
    assert status == 400
    assert not add.called


def test_a_vault_failure_is_502_not_400():
    with patch.object(nova_site, "add_comment", return_value=(False, "could not write comment: 500")), \
            patch.object(nova_site, "audit"):
        status, _, body = _post("/api/comment", {"cycle": 63, "text": "keep it up"})
    assert status == 502
    assert json.loads(body)["ok"] is False


def test_a_failed_comment_is_still_audited_as_an_error():
    with patch.object(nova_site, "add_comment", return_value=(False, "boom")), \
            patch.object(nova_site, "audit") as audit_call:
        _post("/api/comment", {"cycle": 63, "text": "keep it up"})
    assert audit_call.call_args[1]["is_error"] is True


def test_a_comment_must_be_json_like_a_capture():
    """The Content-Type requirement is this endpoint's CSRF defence, not a
    formality -- see the module docstring. It has to hold on every write
    route, not just the first one that documented it."""
    status, _, _ = _post(
        "/api/comment", {"cycle": 63, "text": "x"}, content_type="text/plain"
    )
    assert status == 415


def test_an_oversized_comment_is_refused_before_it_is_read():
    with patch.object(nova_site, "add_comment") as add:
        status, _, _ = _post("/api/comment", {"cycle": 63, "text": "x" * (nova_capture.MAX_BODY_BYTES + 100)})
    assert status == 413
    assert not add.called


# --- one document per entry (issues.md: "stop writing to a huge file") ----
#
# the owner asked for the journal to stop being one 291KB vault document, so
# entries now live one-per-document under `JOURNAL_DIR`. The invariant the
# whole migration rests on is that the split is *lossless*: joining the
# per-entry documents back together must parse to exactly what the
# monolith parsed to. These tests pin that, the filename scheme that makes
# the ordering recoverable, and the fallback that lets the migration and
# the deploy land in either order.


def _plan(markdown):
    from tools.split_journal import plan

    return plan(markdown)


def test_splitting_the_journal_finds_exactly_the_entries_the_parser_does(journal_md):
    assert len(split_entries(journal_md)) == len(parse_journal_file(journal_md))


def test_a_heading_in_the_preamble_is_not_split_out_as_an_entry():
    markdown = "# Journal\n\n### Not an entry\n\n## Entries\n\n### Cycle 5\n\nBody.\n"
    assert [e["heading"] for e in split_entries(markdown)] == ["Cycle 5"]


def test_each_split_entry_is_the_original_text_verbatim(journal_md):
    # No frontmatter, no rewriting, no normalising -- every split entry
    # must appear character for character inside the file it came from.
    # This is what makes the split reversible from the split files alone.
    for entry in split_entries(journal_md):
        assert entry["text"] in journal_md


def test_the_split_reassembles_into_an_identical_entry_list(journal_md):
    assert parse_journal(assemble_entries(_plan(journal_md))) == parse_journal_file(journal_md)


def test_the_oldest_entry_gets_sequence_one_so_numbers_never_shift(journal_md):
    # Entries are newest-first in the file, so sequences are assigned from
    # the back. If they were assigned from the front, every existing
    # document would have to be renamed each time a cycle wrote an entry.
    paths = sorted(_plan(journal_md))
    assert paths[0].endswith("001-2026-08-02-edvard-s-first-message-not-a.md")
    assert paths[-1].endswith("005-cycle-49.md")


def test_filenames_are_zero_padded_so_a_lexical_sort_is_chronological():
    assert entry_filename(9, "Cycle 9") == "009-cycle-9.md"
    assert entry_filename(70, "Cycle 65") == "070-cycle-65.md"
    assert sorted([entry_filename(9, "Cycle 9"), entry_filename(70, "Cycle 65")]) == [
        "009-cycle-9.md",
        "070-cycle-65.md",
    ]


def test_an_entry_with_no_cycle_number_still_gets_a_filename():
    # Three live headings carry no cycle number. Falling back to the
    # sequence alone would be enough to be unique, but the slug is what
    # makes the folder readable in Obsidian.
    assert entry_filename(1, "2026-08-02 — Edvard's first message (not a cycle)") == (
        "001-2026-08-02-edvard-s-first-message-not-a.md"
    )


def test_entries_are_assembled_newest_first_by_sequence_not_by_path():
    files = {
        JOURNAL_DIR + "009-cycle-9.md": "### Cycle 9\n\nOlder.",
        JOURNAL_DIR + "070-cycle-65.md": "### Cycle 65\n\nNewer.",
    }
    assert [e["cycle"] for e in parse_journal(assemble_entries(files))] == [65, 9]


def test_an_unnumbered_file_sorts_oldest_rather_than_being_dropped():
    files = {
        JOURNAL_DIR + "070-cycle-65.md": "### Cycle 65\n\nNewer.",
        JOURNAL_DIR + "hand-written.md": "### Cycle 1\n\nNo sequence prefix.",
    }
    assert [e["cycle"] for e in parse_journal(assemble_entries(files))] == [65, 1]


def test_a_heading_at_the_wrong_depth_is_promoted_rather_than_absorbed():
    # The live failure, 2026-08-13: `162-cycle-146.md` and
    # `163-cycle-147.md` open with `## Cycle N`, two hashes where
    # `_ENTRY_HEADING_RE` needs three, so both were absorbed into the card
    # above them. The assertion that matters is the count -- three files
    # in, three entries out -- because absorbing loses an entry silently
    # while still returning a well-formed page.
    files = {
        JOURNAL_DIR + "162-cycle-146.md": "## Cycle 146 — 2026-08-12 20:35\n\nMine.",
        JOURNAL_DIR + "163-cycle-147.md": "## Cycle 147 — 2026-08-12 21:01\n\nAlso mine.",
        JOURNAL_DIR + "164-cycle-148.md": "### Cycle 148\n\nNot mine.",
    }
    entries = parse_journal(assemble_entries(files))
    assert [e["cycle"] for e in entries] == [148, 147, 146]
    assert entries[0]["body"] == "Not mine."
    assert entries[1]["body"] == "Also mine."
    # The heading's own text survives promotion -- it is a real heading
    # written by a real cycle, and only the hash count was wrong.
    assert entries[2]["date"] == "2026-08-12" and entries[2]["time"] == "20:35"


def test_frontmatter_is_stripped_instead_of_rendering_as_text():
    # `146-cycle-131.md` opens with frontmatter and then `# Cycle 131`,
    # so its `type: log` lines rendered as literal text inside the card
    # for Cycle 132.
    files = {
        JOURNAL_DIR + "146-cycle-131.md": (
            "---\ntype: log\nstatus: built\n---\n\n"
            "# Cycle 131 — 2026-08-12 09:00 Oslo\n\nBody."
        ),
        JOURNAL_DIR + "147-cycle-132.md": "### Cycle 132\n\nNeighbour.",
    }
    entries = parse_journal(assemble_entries(files))
    assert [e["cycle"] for e in entries] == [132, 131]
    assert entries[0]["body"] == "Neighbour."
    assert entries[1]["body"] == "Body."


def test_an_entry_quoting_the_entries_marker_does_not_delete_the_newer_cards():
    """The trap this loop builds for itself: `prompt.md` step 6 tells every
    cycle to append its captures under the `## Entries` marker, so a cycle
    writing honestly about that command puts the marker at the start of a
    line in its own prose. `parse_journal` used to partition on it whatever
    the source was, and the folder has no preamble to partition off -- so
    the cut landed inside Cycle 2's body and everything *above* it went in
    the bin. Above means newer. Measured 2026-08-13 before the fix: three
    documents in, one card out, and the survivor was the oldest.

    This goes through `journal_payload` rather than `parse_journal`
    directly, because the fix is which argument the site passes and a test
    that passes it itself would pin nothing.
    """
    files = {
        JOURNAL_DIR + "003-cycle-3.md": "### Cycle 3\n\nNewest.",
        JOURNAL_DIR + "002-cycle-2.md": (
            "### Cycle 2\n\nI appended my captures:\n\n"
            "## Entries\n\nis the marker that command needs."
        ),
        JOURNAL_DIR + "001-cycle-1.md": "### Cycle 1\n\nOldest.",
    }
    # The cost ledger is `journal_payload`'s second source now (#59); these
    # entries carry no stamp so no runtime resolves either way, but the
    # fetch is real and the network guard is right to refuse it.
    with patch.object(nova_sources, "vault_bulk_fetch", return_value=(VaultFiles(files), {})), \
            patch.object(nova_site, "cost_ledger_json", return_value=""):
        payload = nova_site.journal_payload()
    assert [e["cycle"] for e in payload["entries"]] == [3, 2, 1]
    # Not just present: the quoting entry keeps the text it was quoting,
    # rather than being kept by having the offending half trimmed off it.
    assert "is the marker that command needs." in payload["entries"][1]["body"]


def test_a_corrupt_cost_ledger_costs_runtimes_and_not_the_journal():
    """The failure my own first draft shipped, caught by the whole suite.

    `journal_payload` reads the cost ledger to put a runtime on each card
    (issues.md #59). Wiring `cycle_runtimes` straight in meant a ledger
    that would not parse raised out of the journal build -- so a corrupt
    document belonging to the *costs* page 502'd the journal, which is the
    one page that has to render when other things are broken. Thirty-four
    tests failed at once and every one of them was right.

    The ledger here is markdown rather than JSON on purpose: that is
    exactly what a fixture patching `vault_read_path` for every path
    returns, and it is also what a half-written publish leaves behind.
    """
    files = {
        JOURNAL_DIR + "002-cycle-2.md": "### 2026-08-15 02:14 (Oslo) — Cycle 2\n\nNewest.",
        JOURNAL_DIR + "001-cycle-1.md": "### 2026-08-15 01:10 (Oslo) — Cycle 1\n\nOldest.",
    }
    with patch.object(nova_sources, "vault_bulk_fetch", return_value=(VaultFiles(files), {})), \
            patch.object(nova_site, "cost_ledger_json", return_value="# not json at all"):
        payload = nova_site.journal_payload()
    assert [e["cycle"] for e in payload["entries"]] == [2, 1]
    # And it degrades rather than half-attaching: no card claims a runtime.
    assert all("runtimeSeconds" not in e for e in payload["entries"])


def test_a_cost_ledger_fetch_that_raises_also_leaves_the_journal_standing():
    """The half the corrupt-ledger test does not reach, and the reviewer
    was right that nothing pinned it.

    That test feeds an unparseable document, so the failure happens inside
    `cycle_runtimes` and a narrow `except json.JSONDecodeError` would cover
    it. The reason the catch here is a bare `Exception` is the *other*
    case: `cost_ledger_json()` itself failing -- a vault read erroring
    rather than returning junk. Narrowing it left a background refresh
    thread dying on precisely this, and until now that was a claim in a
    comment rather than something the suite would notice.
    """
    files = {
        JOURNAL_DIR + "001-cycle-1.md": "### 2026-08-15 01:10 (Oslo) — Cycle 1\n\nOnly.",
    }
    with patch.object(nova_sources, "vault_bulk_fetch", return_value=(VaultFiles(files), {})), \
            patch.object(nova_site, "cost_ledger_json",
                         side_effect=RuntimeError("vault unreachable")):
        payload = nova_site.journal_payload()
    assert [e["cycle"] for e in payload["entries"]] == [1]
    assert "runtimeSeconds" not in payload["entries"][0]


def test_a_good_cost_ledger_puts_a_runtime_on_the_card_it_belongs_to():
    """The positive control for the test above.

    Without this, "no entry has a runtime" passes because the wiring was
    deleted, not because the ledger was corrupt -- the same shape as the
    `.prio-picker` assertion Cycle 202 shipped against markup that never
    rendered it. Same two entries, same call, a ledger that parses.
    """
    files = {
        JOURNAL_DIR + "002-cycle-2.md": "### 2026-08-15 02:14 (Oslo) — Cycle 2\n\nNewest.",
        JOURNAL_DIR + "001-cycle-1.md": "### 2026-08-15 01:10 (Oslo) — Cycle 1\n\nOldest.",
    }
    ledger = json.dumps({"cycles": [
        {"startedAt": "2026-08-14T23:00:00Z", "durationSeconds": 600.0},   # 01:00 Oslo
        {"startedAt": "2026-08-15T00:00:00Z", "durationSeconds": 940.0},   # 02:00 Oslo
    ]})
    with patch.object(nova_sources, "vault_bulk_fetch", return_value=(VaultFiles(files), {})), \
            patch.object(nova_site, "cost_ledger_json", return_value=ledger):
        payload = nova_site.journal_payload()
    got = {e["cycle"]: e.get("runtimeSeconds") for e in payload["entries"]}
    assert got == {2: 940, 1: 600}


def test_the_journal_never_reads_the_emptied_archive_again():
    """`journal.md` is a 614-byte signpost, not a journal, and has been
    since 2026-08-10.

    `journal_markdown` used to fall back to it when the folder came back
    empty. The two facts together are what made that dangerous: the branch
    only ran when the folder read had *failed*, and the thing it fell back
    to could no longer answer, so the failure rendered as a journal with
    nothing in it. Pinned with the real file's shape -- a preamble that
    deliberately opens with a `### ` heading documenting the entry format,
    which the old fallback had to work to avoid turning into a card.

    An empty folder is now an empty journal, full stop, and nothing reads
    `JOURNAL_PATH` at all."""
    archive = (
        "# Journal\n\nWrite entries like:\n\n### 2026-01-01 00:00 (Oslo) — Cycle 0\n\n"
        "Nothing here to read, and nothing to append."
    )
    with patch.object(nova_sources, "vault_bulk_fetch", return_value=(VaultFiles(), {})), \
            patch.object(nova_sources, "vault_read_path", return_value=archive) as read:
        payload = nova_site.journal_payload()
    assert payload["entries"] == []
    assert JOURNAL_PATH not in [call.args[0] for call in read.call_args_list]


def test_a_folder_the_vault_could_not_fully_read_is_an_error_not_an_empty_journal():
    """The failure the fallback was hiding, stated directly.

    `vault_bulk_fetch` reports a database it could not reach on
    `.unreadable` and returns whatever else it got. For the journal that
    is not a partial answer a reader can use -- an entry list missing an
    unknown number of entries is indistinguishable from a loop that did
    not run -- so this is the one caller that must refuse it. The reason
    travels with the refusal, because "the journal is empty" and "the
    journal database would not answer" need to look different to whoever
    opens the page."""
    files = VaultFiles(
        {JOURNAL_DIR + "070-cycle-65.md": "### Cycle 65\n\nThe half that survived."},
        unreadable=["_all_docs include_docs batch failed on database 'nova' (503)"],
    )
    with patch.object(nova_sources, "vault_bulk_fetch", return_value=(files, {})):
        with pytest.raises(RuntimeError, match="could not be fully read"):
            nova_sources.journal_markdown()
        with pytest.raises(RuntimeError, match=r"database 'nova' \(503\)"):
            nova_sources.journal_markdown()


def test_a_journal_the_vault_could_not_read_is_a_502_rather_than_an_empty_feed():
    """End to end, because the raise is only worth anything if the server
    turns it into something a reader can see. `nova_site`'s handler already
    maps an exception to a 502 carrying the message -- this pins that the
    journal endpoint reaches it rather than serving 200 with no entries."""
    files = VaultFiles(unreadable=["content chunk batch failed on database 'nova' (500)"])
    nova_site.reset_cache()
    with patch.object(nova_sources, "vault_bulk_fetch", return_value=(files, {})):
        status, _, body = _get("/api/journal")
    nova_site.reset_cache()
    assert status == 502
    assert "database 'nova'" in json.loads(body)["error"]


def test_a_document_with_no_heading_at_all_gets_one_from_its_filename():
    files = {
        JOURNAL_DIR + "070-cycle-65.md": "### Cycle 65\n\nNeighbour.",
        JOURNAL_DIR + "071-cycle-66.md": "Straight into prose, no heading.",
        JOURNAL_DIR + "001-edvard-s-first-message.md": "No heading and no cycle.",
    }
    entries = parse_journal(assemble_entries(files))
    assert [e["cycle"] for e in entries] == [66, 65, None]
    assert entries[0]["body"] == "Straight into prose, no heading."
    assert entries[1]["body"] == "Neighbour."
    assert entries[2]["body"] == "No heading and no cycle."
    # Named directly, because the cycle number reaching the card through
    # `assemble_entries` above does not say *which* part of the filename
    # it came from -- the first mutation run got a green suite out of a
    # `synthetic_heading` that ignored the filename entirely.
    assert synthetic_heading(JOURNAL_DIR + "071-cycle-66.md") == "Cycle 66"
    assert synthetic_heading(JOURNAL_DIR + "001-edvard-s-first-message.md") == (
        "Edvard s first message"
    )


def test_a_correct_document_is_left_exactly_as_written():
    # The 161 live files that already do the right thing must round-trip
    # untouched -- normalisation that rewrote them would be a silent
    # rewrite of the archive on every page load.
    text = "### Cycle 65 — 2026-08-09\n\nBody with a ## line in it.\n\n---\nPR: #23 | Outcome: merged"
    assert normalise_entry(JOURNAL_DIR + "070-cycle-65.md", text) == text


def test_a_footer_written_at_the_top_and_bolded_still_becomes_a_badge():
    # Cycles 146 and 147, live: `**PR: ... | Outcome: ...**` directly
    # under the heading. Not a parse error -- both cards rendered with no
    # PR and no outcome, which reads as a cycle that shipped nothing.
    # Two hashes, not three, because that is what the live document says
    # -- the heading only becomes `###` via `normalise_entry`'s promotion,
    # and a fixture written with three tests the repair in isolation from
    # the composition it actually has to survive.
    files = {
        JOURNAL_DIR + "162-cycle-146.md": (
            "## Cycle 146 — 2026-08-12 20:35\n\n"
            "**PR: runner#128 | Outcome: merged**\n\n"
            "The item at the top of the handoff is done."
        ),
    }
    entries = parse_journal(assemble_entries(files))
    assert len(entries) == 1
    assert entries[0]["pr"] == "runner#128"
    assert entries[0]["outcome"] == "merged"
    # And the body no longer carries the line as prose, in either place.
    assert "PR:" not in entries[0]["body"]
    assert entries[0]["body"] == "The item at the top of the handoff is done."


def test_a_hard_wrapped_footer_keeps_the_half_below_the_line_break():
    # Entry 004, live: a correct footer, hard-wrapped. `_FOOTER_RE` misses
    # it because `$` lands on the continuation line. Matching a line at a
    # time repairs the badge and truncates the outcome mid-sentence, so
    # the paragraph is what gets matched.
    files = {
        JOURNAL_DIR + "004-edvard-s-first-message.md": (
            "### 2026-08-02 — Edvard's first message\n\n"
            "I'd rather ask.\n\n"
            "---\nPR: #31 | Outcome: open — green, deliberately unmerged\n"
            "so this reply survives"
        ),
    }
    entries = parse_journal(assemble_entries(files))
    assert entries[0]["pr"] == "#31"
    assert entries[0]["outcome"] == "open"
    assert entries[0]["outcomeDetail"] == (
        "green, deliberately unmerged so this reply survives"
    )
    # The `---` was that footer's own rule; leaving it behind draws a line
    # across the card with nothing under it.
    assert entries[0]["body"] == "I'd rather ask."


def test_the_real_hard_wrapped_footer_in_the_fixture_extracts_the_real_values(journal_md):
    # `test_the_split_reassembles_into_an_identical_entry_list` runs the
    # real entry-004 text, but it compares `parse_journal` to itself: a
    # wrong extraction moves both sides equally and it stays green. So the
    # concrete values are pinned here, against the real committed fixture
    # rather than against a shorter wrap I made up.
    entries = parse_journal_file(journal_md)
    first = [e for e in entries if e["title"].startswith("Edvard's first message")]
    assert len(first) == 1
    assert first[0]["pr"] == "#31"
    assert first[0]["outcome"] == "open"
    assert first[0]["outcomeDetail"] == (
        "green, deliberately unmerged so this reply survives; next cycle "
        "should merge it as its first act and expect to die"
    )


def test_the_rule_quoted_as_a_plain_paragraph_is_not_promoted_to_a_badge():
    # The hole the fence guard alone left open, found by a reviewer. This
    # journal quotes rule text as unfenced prose constantly, and the entry
    # below forgot its own footer -- so before the ends-only rule, the
    # example's own `#23 (or "none")` became the card's badge.
    files = {
        JOURNAL_DIR + "070-cycle-65.md": (
            "### Cycle 65\n\nThe rule says end with a line shaped like this:\n\n"
            'PR: #23 (or "none") | Outcome: merged / shipped / stuck / no-op\n\n'
            "and I meant to fill it in for real but ran out of time."
        ),
    }
    entries = parse_journal(assemble_entries(files))
    assert entries[0]["pr"] == ""
    assert entries[0]["outcome"] == ""
    assert 'PR: #23 (or "none")' in entries[0]["body"]


def test_a_footer_quoted_in_a_code_fence_is_not_promoted_to_a_badge():
    # `personality.md` states the footer format as a fenced block, so an
    # entry quoting it is a thing a cycle would plausibly write. A missing
    # badge is honest; a badge invented out of an example is not.
    #
    # The blank lines inside the fence are the whole test. Written without
    # them -- the way `personality.md` actually prints the block -- the
    # fence markers join the same paragraph as the footer and the match
    # fails on the leading backticks, so deleting the fence guard
    # entirely left the suite green. That is the tests being blind, not
    # the guard being unreachable: a fence can contain blank lines, and
    # then this line is a paragraph of its own and the guard is the only
    # thing standing between it and a badge.
    files = {
        JOURNAL_DIR + "070-cycle-65.md": (
            "### Cycle 65\n\nThe rule says end with:\n\n"
            "```\n\nPR: #23 | Outcome: merged\n\n```\n\nand I forgot to."
        ),
    }
    entries = parse_journal(assemble_entries(files))
    assert entries[0]["pr"] == ""
    assert entries[0]["outcome"] == ""
    assert "PR: #23 | Outcome: merged" in entries[0]["body"]


def test_the_same_quote_without_blank_lines_is_also_left_alone():
    # The shape `personality.md` really prints, kept alongside the one
    # above so the two protections stay distinguishable: here it is the
    # paragraph join, not the fence guard, that refuses.
    files = {
        JOURNAL_DIR + "070-cycle-65.md": (
            "### Cycle 65\n\nThe rule says end with:\n\n"
            "```\n---\nPR: #23 | Outcome: merged\n```\n\nand I forgot to."
        ),
    }
    entries = parse_journal(assemble_entries(files))
    assert entries[0]["pr"] == ""
    assert "PR: #23 | Outcome: merged" in entries[0]["body"]


def test_two_candidate_footers_are_left_alone_rather_than_picked_between():
    files = {
        JOURNAL_DIR + "070-cycle-65.md": (
            # Both at an end, so the ends-only rule cannot be what refuses
            # here -- the count is.
            "### Cycle 65\n\n**PR: #1 | Outcome: merged**\n\n"
            "I then changed my mind.\n\n**PR: #2 | Outcome: stuck**"
        ),
    }
    entries = parse_journal(assemble_entries(files))
    assert entries[0]["pr"] == ""
    assert "PR: #1" in entries[0]["body"]
    assert "PR: #2" in entries[0]["body"]


def test_an_entry_that_ends_correctly_keeps_its_own_footer():
    # The guard that makes the rest of this safe: if `_FOOTER_RE` can
    # already read a footer, nothing moves, whatever else the entry says.
    files = {
        JOURNAL_DIR + "070-cycle-65.md": (
            "### Cycle 65\n\nLast hour I wrote\n\n**PR: #1 | Outcome: merged**\n\n"
            "and it was wrong.\n\n---\nPR: #2 | Outcome: shipped"
        ),
    }
    entries = parse_journal(assemble_entries(files))
    assert entries[0]["pr"] == "#2"
    assert entries[0]["outcome"] == "shipped"
    assert "**PR: #1 | Outcome: merged**" in entries[0]["body"]


def test_the_repair_reaches_the_frozen_archive_too_not_just_the_folder():
    # The repair sits in `parse_journal`, not in `normalise_entry`, and
    # this is why: `normalise_entry` only runs on the per-entry documents,
    # so putting it there gave the folder better cards than the monolith
    # built from the same text -- which is exactly what
    # `test_the_split_reassembles_into_an_identical_entry_list` is for,
    # and it caught the divergence.
    markdown = "## Entries\n\n### Cycle 65\n\n**PR: #1 | Outcome: merged**\n\nBody."
    entries = parse_journal_file(markdown)
    assert entries[0]["pr"] == "#1"
    assert entries[0]["body"] == "Body."


def test_an_entry_body_containing_a_two_hash_line_is_not_split():
    # Why this is fixed per-document rather than by loosening
    # `_ENTRY_HEADING_RE` to accept `##`: entries are free prose and some
    # of them quote headings.
    files = {
        JOURNAL_DIR + "070-cycle-65.md": "### Cycle 65\n\nI edited:\n\n## Needs Edvard\n\nand stopped.",
    }
    entries = parse_journal(assemble_entries(files))
    assert len(entries) == 1
    assert "## Needs Edvard" in entries[0]["body"]


def test_the_single_entry_fetch_normalises_the_same_way_the_folder_does(monkeypatch):
    # The reply worker's fast path. Without this it parses to zero
    # entries and falls back to the full journal, where -- before this
    # change -- the entry was absorbed into its neighbour and so was not
    # found there either, leaving the owner's reply written with no memory
    # of the entry he was replying to.
    from agora_runner import nova_sources

    monkeypatch.setattr(
        nova_sources, "vault_list_ids", lambda prefix: [JOURNAL_DIR + "163-cycle-147.md"]
    )
    monkeypatch.setattr(
        nova_sources, "vault_read_path", lambda path: "## Cycle 147\n\nWhat I did."
    )
    entries = parse_journal(nova_sources.journal_entry_markdown(147))
    assert [e["cycle"] for e in entries] == [147]
    assert entries[0]["body"] == "What I did."


def test_the_migration_refuses_to_write_when_two_entries_collide():
    from tools.split_journal import verify

    markdown = "## Entries\n\n### Cycle 5\n\nOne.\n\n### Cycle 5\n\nTwo.\n"
    files = {JOURNAL_DIR + "001-cycle-5.md": "### Cycle 5\n\nOne."}
    with pytest.raises(SystemExit, match="share a filename"):
        verify(markdown, files)


def test_the_migration_does_not_refuse_an_entry_that_quotes_the_marker():
    """`verify` parses two different kinds of thing and must read each as
    what it is: `markdown` is a real `journal.md` with a real preamble,
    and `assemble_entries(files)` is an entries body with none. Reading
    both the same way is the bug this PR is about, and here it surfaces
    as a false accusation rather than a lost card -- the split aborts with
    "renders differently" pointing at an entry that is perfectly fine, and
    `main` has no handler, so the tool is simply unusable against a folder
    containing such an entry. Found by the reviewer, who reproduced it."""
    from tools.split_journal import verify

    markdown = (
        "# Journal\n\nPreamble.\n\n## Entries\n\n"
        "### Cycle 5\n\nOne.\n\n"
        "### Cycle 4\n\nI appended my captures:\n\n## Entries\n\nis the marker.\n"
    )
    files = dict(_plan(markdown))
    assert verify(markdown, files) == 2


def test_the_migration_refuses_to_write_when_an_entry_would_render_differently():
    from tools.split_journal import verify

    markdown = "## Entries\n\n### Cycle 5\n\nOne.\n\n### Cycle 4\n\nTwo.\n"
    files = dict(_plan(markdown))
    # Losing the footer is the kind of silent corruption the check exists
    # for: same entry count, same headings, different rendered entry.
    files[JOURNAL_DIR + "002-cycle-5.md"] = "### Cycle 5\n\nSomething else."
    with pytest.raises(SystemExit, match="renders differently"):
        verify(markdown, files)


def test_the_site_reads_the_per_entry_documents_when_they_exist():
    with patch.object(nova_sources, "vault_bulk_fetch") as bulk, \
            patch.object(nova_sources, "vault_read_path") as monolith:
        bulk.return_value = (VaultFiles({JOURNAL_DIR + "070-cycle-65.md": "### Cycle 65\n\nSplit."}), {})
        assert "Split." in nova_site.journal_markdown()
        bulk.assert_called_once_with(JOURNAL_DIR, with_mtimes=True)
        monolith.assert_not_called()


def test_the_folder_is_the_only_journal_source_there_is():
    # There was a second one, for exactly as long as the migration needed
    # it: the deploy and the split were two separate acts and either could
    # land first, so until the folder had anything in it the monolith was
    # still the journal. Both landed on 2026-08-09 and the monolith was
    # emptied the day after, which turned the safety net into a hole --
    # see `test_the_journal_never_reads_the_emptied_archive_again`.
    with patch.object(nova_sources, "vault_bulk_fetch") as bulk, \
            patch.object(nova_sources, "vault_read_path") as monolith:
        bulk.return_value = (VaultFiles(), {})
        assert nova_site.journal_markdown() == ""
        monolith.assert_not_called()


# --- compression -----------------------------------------------------------
#
# Measured against the live pod on 2026-08-10, before any of this existed:
# a cold load was 588,998 bytes with no `Content-Encoding` on any of the
# six responses, while the browser asking for them was already sending
# `Accept-Encoding: gzip, deflate, br, zstd`.


def test_a_real_browsers_header_gets_gzip_and_the_same_json_back(journal_md):
    """The whole point, and deliberately asserted through the *browser's*
    header rather than a bare `gzip`.

    Cycle 70 shipped four green gzip tests for a path production never
    took: Express's `compression` picked Brotli out of exactly this header
    and every test covered the fallback. This server offers one encoding,
    so that particular gap cannot open -- but the test that would have
    caught it costs one line, so it is the one written.
    """
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        status, head, body = _get("/api/journal", BROWSER_ACCEPT_ENCODING)
    assert status == 200
    assert "Content-Encoding: gzip" in head
    assert "Vary: Accept-Encoding" in head

    inflated = gzip.decompress(body)
    assert len(body) < len(inflated)
    payload = json.loads(inflated)
    assert len(payload["entries"]) == 5
    assert payload["status"]["cycle"] == 49


def test_the_compressed_body_is_byte_for_byte_what_the_plain_one_says(journal_md):
    """A compression bug that loses or reorders content would still parse.
    This pins the two responses to the same bytes, not the same shape."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, _, plain = _get("/api/journal")
        _, _, compressed = _get("/api/journal", BROWSER_ACCEPT_ENCODING)
    assert gzip.decompress(compressed) == plain


def test_a_client_that_does_not_ask_still_gets_plain_bytes(journal_md):
    """This is the runner's own urllib path and every non-browser caller:
    urllib sends no `Accept-Encoding` at all. Breaking it would break
    every future cycle, which is the failure mode worth a named test."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        status, head, body = _get("/api/journal")
    assert status == 200
    assert "Content-Encoding" not in head
    json.loads(body)  # plain, parseable, no inflate step


def test_gzip_with_q_nought_means_no_gzip(journal_md):
    """`gzip;q=0` contains the string "gzip" and forbids it. A substring
    check would read this header as consent."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        status, head, body = _get("/api/journal", "Accept-Encoding: gzip;q=0, identity\r\n")
    assert status == 200
    assert "Content-Encoding" not in head
    json.loads(body)


def test_a_body_too_small_to_be_worth_it_is_left_alone():
    """`/api/comments` is 15 bytes on the live pod and gzips to 35.
    Compression is not free below the threshold, it is negative."""
    with patch.object(nova_site, "comments_payload", return_value={}):
        status, head, body = _get("/api/comments", BROWSER_ACCEPT_ENCODING)
    assert status == 200
    assert "Content-Encoding" not in head
    assert len(body) < MIN_COMPRESS_BYTES
    assert json.loads(body) == {}


def test_vary_is_sent_even_when_the_response_came_back_plain(journal_md):
    """Without this a shared cache can hand a gzipped body to a client
    that never asked for one. It describes the endpoint, not the reply."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, head, _ = _get("/api/journal")
    assert "Vary: Accept-Encoding" in head


def test_the_static_shell_compresses_too():
    """app.js and style.css are 28KB and 12KB of text served on every
    cold load; they were as uncompressed as the journal was."""
    for path in ("/app.js", "/style.css", "/"):
        status, head, body = _get(path, BROWSER_ACCEPT_ENCODING)
        assert status == 200, path
        assert "Content-Encoding: gzip" in head, path
        assert len(gzip.decompress(body)) > len(body), path


def test_head_reports_the_length_of_the_body_a_get_would_send(journal_md):
    """`do_HEAD` runs the whole of `do_GET` and drops the body, so the
    Content-Length it advertises has to be the compressed one."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, get_head, get_body = _get("/api/journal", BROWSER_ACCEPT_ENCODING)
        _, head_head, head_body = _get("/api/journal", BROWSER_ACCEPT_ENCODING, method="HEAD")
    assert head_body == b""
    assert "Content-Encoding: gzip" in head_head
    advertised = re.search(r"Content-Length: (\d+)", head_head).group(1)
    assert int(advertised) == len(get_body)


def test_the_same_content_compresses_to_the_same_bytes(journal_md):
    """gzip stamps the current time into its header unless told not to,
    so an unchanged journal would be a different response every second.

    This asserts the MTIME field rather than only comparing two
    responses, which is what the first version did: two requests inside
    the same wall-clock second produce identical bytes *even with the
    bug*, so that version went red only 1 run in 5 against a
    deliberately broken build. A control that agrees with you four times
    out of five is not a control.
    """
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, _, first = _get("/api/journal", BROWSER_ACCEPT_ENCODING)
        _, _, second = _get("/api/journal", BROWSER_ACCEPT_ENCODING)
    assert first == second
    # Bytes 4-8 of a gzip member are MTIME, little-endian. Zero means "no
    # timestamp", and is the only value that is stable across rebuilds.
    assert first[:2] == b"\x1f\x8b"
    assert int.from_bytes(first[4:8], "little") == 0


@pytest.mark.parametrize(
    "header,expected",
    [
        ("gzip, deflate, br, zstd", True),   # Chrome / Firefox / Safari
        ("deflate, gzip", True),             # curl --compressed
        ("gzip;q=1.0, identity;q=0.5", True),
        ("GZIP", True),                      # tokens are case-insensitive
        ("  gzip  ", True),
        ("*", True),
        ("br, *;q=0.1", True),
        (None, False),                       # urllib: no header at all
        ("", False),
        ("identity", False),
        ("br, zstd", False),
        ("gzip;q=0", False),
        ("gzip;q=0.0", False),
        ("*;q=0", False),
        ("br;q=1.0, gzip;q=0", False),       # names it only to refuse it
        ("gzipped", False),                  # not the gzip token
    ],
)
def test_accept_encoding_is_parsed_not_pattern_matched(header, expected):
    assert nova_site.accepts_gzip(header) is expected


# --- Replying to Needs Edvard over HTTP (2026-08-10) ------------------------  (not-prose: quoting a literal)
#
# `{"target": "needs"}` instead of a `cycle`. The boundary is the same one
# the rest of this endpoint holds: `target` is checked against a one-value
# allow-list, so it selects a heading and never a document.


def test_the_comments_endpoint_serves_needs_replies_separately():
    stored = (
        "## New\n\n"
        "### Needs Edvard · 2026-08-10 08:20\n\ngo ahead and do it\n\n"
        "### Cycle 63 · 2026-08-09 22:40\n\nkeep it up\n\n"
        "## Acknowledged\n"
    )
    with patch.object(nova_sources, "vault_read_path", return_value=stored):
        status, _, body = _get("/api/comments")
    assert status == 200
    payload = json.loads(body)
    # The needs reply must not be filed under a cycle, or the client would
    # paint it onto whichever journal card that key names.
    assert list(payload["byCycle"]) == ["63"]
    assert [c["text"] for c in payload["needs"]] == ["go ahead and do it"]


def test_a_needs_reply_reaches_the_vault_without_a_cycle():
    with patch.object(nova_site, "add_needs_comment", return_value=(True, "commented on needs edvard")) as add, \
            patch.object(nova_site, "add_comment") as add_cycle, \
            patch.object(nova_site, "audit"):
        status, _, body = _post("/api/comment", {"target": "needs", "text": "go ahead and do it"})
    assert status == 200
    assert json.loads(body)["ok"] is True
    assert add.call_args[0] == ("go ahead and do it",)
    assert not add_cycle.called


def test_a_needs_reply_is_audited_with_what_was_typed():
    with patch.object(nova_site, "add_needs_comment", return_value=(True, "ok")), \
            patch.object(nova_site, "audit") as audit_call:
        _post("/api/comment", {"target": "needs", "text": "go ahead and do it"})
    assert audit_call.called
    assert audit_call.call_args[1]["after"] == "go ahead and do it"
    assert "Needs Edvard" in audit_call.call_args[0][3]
    assert audit_call.call_args[1]["is_error"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"target": "needs"},                       # no text at all
        {"target": "needs", "text": 12},           # text that is not a string
        {"target": "needs", "text": "   \n  "},    # nothing but whitespace
        {"target": "issues", "text": "x"},         # a capture target, not a comment one
        {"target": "../../etc/passwd", "text": "x"},
        {"target": 1, "text": "x"},
    ],
)
def test_a_malformed_needs_reply_is_400_and_never_touches_the_vault(payload):
    with patch.object(nova_site, "add_needs_comment") as add, \
            patch.object(nova_site, "add_comment") as add_cycle:
        status, _, _ = _post("/api/comment", payload)
    assert status == 400
    assert not add.called and not add_cycle.called


def test_a_needs_target_wins_over_a_cycle_in_the_same_payload():
    """Only one of the two can be honoured. `target` is the explicit one,
    and silently filing his answer under a stray cycle number is the exact
    failure the separate heading exists to prevent."""
    with patch.object(nova_site, "add_needs_comment", return_value=(True, "ok")) as add, \
            patch.object(nova_site, "add_comment") as add_cycle, \
            patch.object(nova_site, "audit"):
        status, _, _ = _post("/api/comment", {"target": "needs", "cycle": 63, "text": "x"})
    assert status == 200
    assert add.called and not add_cycle.called


def test_a_failed_needs_reply_is_still_audited_as_an_error():
    with patch.object(nova_site, "add_needs_comment", return_value=(False, "409 conflict")), \
            patch.object(nova_site, "audit") as audit_call:
        status, _, _ = _post("/api/comment", {"target": "needs", "text": "go ahead"})
    assert status == 502
    assert audit_call.call_args[1]["is_error"] is True


# --- the instant reply (2026-08-10) ---------------------------------------
#
# The endpoint's job here is small and the two halves of it are worth
# pinning separately: a comment on a cycle asks for a reply, and everything
# else does not. The generation itself is tested in test_nova_replies.py --
# what these defend is that the request never blocks on it and that the
# stamp the reply is keyed on is the stamp the comment was stored with.


def test_a_comment_on_a_cycle_asks_for_a_reply_with_the_stamp_it_was_stored_with():
    """The stamp is the join between the two: store it under one and queue
    the reply under another and the worker finds nothing to reply to."""
    with patch.object(nova_site, "add_comment", return_value=(True, "ok")) as add, \
            patch.object(nova_site, "enqueue_reply") as enqueue, \
            patch.object(nova_site, "audit"):
        _post("/api/comment", {"cycle": 63, "text": "keep it up"})
    assert enqueue.call_args[0] == (63, add.call_args[0][2])


def test_a_needs_edvard_answer_is_not_replied_to():
    """That block is Nova asking him a question. His answer is a decision
    for a cycle to act on, and a paragraph back would sit where the work
    belongs."""
    with patch.object(nova_site, "add_needs_comment", return_value=(True, "ok")), \
            patch.object(nova_site, "enqueue_reply") as enqueue, \
            patch.object(nova_site, "audit"):
        _post("/api/comment", {"target": "needs", "text": "yes, do it"})
    assert not enqueue.called


def test_a_comment_that_did_not_reach_the_vault_is_not_replied_to():
    """Replying to a comment that was never stored would key on a stamp no
    comment carries -- a guaranteed-wasted CLI turn."""
    with patch.object(nova_site, "add_comment", return_value=(False, "couchdb down")), \
            patch.object(nova_site, "enqueue_reply") as enqueue, \
            patch.object(nova_site, "audit"):
        status, _, _ = _post("/api/comment", {"cycle": 63, "text": "keep it up"})
    assert status == 502
    assert not enqueue.called


def test_a_rejected_comment_is_not_replied_to():
    with patch.object(nova_site, "enqueue_reply") as enqueue, \
            patch.object(nova_site, "audit"):
        status, _, _ = _post("/api/comment", {"cycle": 63, "text": "   "})
    assert status == 400
    assert not enqueue.called


def test_the_comments_endpoint_says_which_replies_are_still_coming():
    """`replyPending` is what puts "Nova is replying…" on the card. It comes
    from the server rather than being remembered by the client, so the line
    survives a reload and shows on a second device -- the wait can be the
    length of a whole cycle, because the bridge runs one CLI call at a time."""
    stored = (
        "## New\n\n"
        "### Cycle 57 · 2026-08-09 16:02\n\nwaiting on this one\n\n"
        "### Cycle 55 · 2026-08-09 13:10\n\nalready answered\n\n"
        "#### Nova · 2026-08-09 13:12\n\nhere you go\n"
    )
    with patch.object(nova_sources, "vault_read_path", return_value=stored), \
            patch.object(nova_site, "pending_since", return_value={(57, "2026-08-09 16:02"): time.time()}), \
            patch.object(nova_site, "failed_replies", return_value={}):
        status, _, body = _get("/api/comments")
    assert status == 200
    payload = json.loads(body)
    assert payload["byCycle"]["57"][0]["replyPending"] is True
    assert payload["byCycle"]["57"][0]["reply"] == ""
    answered = payload["byCycle"]["55"][0]
    assert answered["replyPending"] is False
    assert answered["reply"] == "here you go"


def test_a_long_wait_is_flagged_rather_than_called_a_reply_being_written():
    """The owner, on cycle 81: "Nova is replying..." should only be visible if
    its actually working on replying". Past the threshold something is
    holding it up. Which thing is deliberately not asserted here -- see
    comments_payload and issue #80."""
    stored = (
        "## New\n\n"
        "### Cycle 57 \u00b7 2026-08-09 16:02\n\nwaiting on this one\n\n"
        "### Cycle 55 \u00b7 2026-08-09 13:10\n\njust asked\n"
    )
    old = time.time() - nova_site.WAITING_AFTER_SECONDS - 1
    with patch.object(nova_sources, "vault_read_path", return_value=stored), \
            patch.object(nova_site, "pending_since", return_value={
                (57, "2026-08-09 16:02"): old,
                (55, "2026-08-09 13:10"): time.time(),
            }), \
            patch.object(nova_site, "failed_replies", return_value={}):
        status, _, body = _get("/api/comments")
    assert status == 200
    payload = json.loads(body)
    queued = payload["byCycle"]["57"][0]
    assert queued["replyPending"] is True and queued["replyWaiting"] is True
    fresh = payload["byCycle"]["55"][0]
    assert fresh["replyPending"] is True and fresh["replyWaiting"] is False


def test_a_waiting_reply_carries_how_long_it_has_waited():
    """The card used to name a cause the server cannot see -- "queued behind
    a running cycle" -- when the bridge takes a parallel lane except in the
    refresh window. The elapsed second is what this server actually knows,
    so it is what goes out."""
    stored = (
        "## New\n\n"
        "### Cycle 57 \u00b7 2026-08-09 16:02\n\nwaiting on this one\n\n"
        "### Cycle 55 \u00b7 2026-08-09 13:10\n\nanswered\n"
    )
    with patch.object(nova_sources, "vault_read_path", return_value=stored), \
            patch.object(nova_site, "pending_since", return_value={
                (57, "2026-08-09 16:02"): time.time() - 185,
            }), \
            patch.object(nova_site, "failed_replies", return_value={}):
        status, _, body = _get("/api/comments")
    assert status == 200
    payload = json.loads(body)
    waiting = payload["byCycle"]["57"][0]
    assert waiting["replyWaiting"] is True
    # Wall-clock passes between the patch and the read, so pin the band.
    assert 185 <= waiting["replyWaitingSeconds"] <= 190
    # A comment nobody is waiting on reports zero, never a negative or a
    # stray clock reading the card would render as a real wait.
    assert payload["byCycle"]["55"][0]["replyWaitingSeconds"] == 0


def test_a_backwards_clock_step_does_not_put_a_negative_wait_on_the_wire():
    """Both ends of the subtraction are `time.time()`, so an NTP correction
    between enqueue and this read can invert it. A wait cannot have lasted
    -50 seconds, whatever the consumer happens to do with one."""
    stored = "## New\n\n### Cycle 57 \u00b7 2026-08-09 16:02\n\nasked\n"
    with patch.object(nova_sources, "vault_read_path", return_value=stored), \
            patch.object(nova_site, "pending_since", return_value={
                (57, "2026-08-09 16:02"): time.time() + 300,
            }), \
            patch.object(nova_site, "failed_replies", return_value={}):
        status, _, body = _get("/api/comments")
    assert status == 200
    assert json.loads(body)["byCycle"]["57"][0]["replyWaitingSeconds"] == 0


def test_a_reply_that_failed_says_so_instead_of_vanishing():
    stored = "## New\n\n### Cycle 57 \u00b7 2026-08-09 16:02\n\nno answer coming\n"
    with patch.object(nova_sources, "vault_read_path", return_value=stored), \
            patch.object(nova_site, "pending_since", return_value={}), \
            patch.object(nova_site, "failed_replies", return_value={(57, "2026-08-09 16:02"): "bridge down"}):
        status, _, body = _get("/api/comments")
    payload = json.loads(body)
    item = payload["byCycle"]["57"][0]
    assert item["replyPending"] is False
    assert item["replyFailed"] is True


# --- the card's time, measured instead of typed --------------------------
#
# the owner, issues.md 2026-08-10: "I actually see in Agora that the cycle 86
# did start precisely at 19:00 at only ran for 7 minutes. But the Journal
# said 19:30. Thats wierd."
#
# It was: the stamp came straight out of the `### ` heading, which a cycle
# types by hand at the end of its run, so it was a guess at a finish time
# and it was always ahead. The vault document's own mtime is the one thing
# here that was measured.


def test_the_entry_time_comes_from_the_documents_mtime_not_the_typed_heading():
    from agora_runner.nova_journal import entry_times

    # 2026-08-10 19:06:12 Oslo, the minute Cycle 86 actually wrote.
    mtimes = {JOURNAL_DIR + "093-cycle-86.md": 1786381560000}
    times = entry_times(mtimes)
    assert times == {86: [("2026-08-10", "19:06")]}

    entry = parse_journal(
        "### 2026-08-10 19:30 (Oslo) — Cycle 86 — Comments read downwards\n\nProse.",
        times,
    )[0]
    assert (entry["date"], entry["time"]) == ("2026-08-10", "19:06")
    assert entry["title"] == "Comments read downwards"


def test_a_heading_with_no_cycle_number_keeps_its_typed_stamp():
    # The owner's own messages and the odd addendum carry no cycle number, so
    # there is no file to join them to. Borrowing another entry's time
    # would be worse than the guess it replaced.
    times = {86: [("2026-08-10", "19:06")]}
    entry = parse_journal("### 2026-08-02 — Edvard's first message\n\nProse.", times)[0]
    assert (entry["date"], entry["time"]) == ("2026-08-02", "")


def test_a_cycle_that_wrote_twice_gets_two_times_in_file_order():
    # Cycle 81 wrote an entry and then an addendum: two documents, two
    # entries, one cycle number. Newest-first on both sides, so the nth
    # entry takes the nth write time rather than both collapsing onto one.
    from agora_runner.nova_journal import entry_times

    times = entry_times({
        JOURNAL_DIR + "087-cycle-81.md": 1786365960000,          # 14:46
        JOURNAL_DIR + "088-cycle-81-addendum.md": 1786366080000,  # 14:48
    })
    assert times == {81: [("2026-08-10", "14:48"), ("2026-08-10", "14:46")]}

    markdown = (
        "### 2026-08-10 14:40 (Oslo) — Cycle 81, addendum\n\nSecond.\n\n"
        "### 2026-08-10 14:30 (Oslo) — Cycle 81\n\nFirst."
    )
    assert [e["time"] for e in parse_journal(markdown, times)] == ["14:48", "14:46"]


def test_an_entry_with_no_mtime_keeps_the_heading_it_typed():
    # The pre-split archive is one file with no per-entry documents, and a
    # damaged doc is omitted from the bulk fetch entirely. Either way the
    # typed stamp is all the site has ever had for those.
    entry = parse_journal("### 2026-08-09 04:20 (Oslo) — Cycle 49\n\nProse.", {})[0]
    assert (entry["date"], entry["time"]) == ("2026-08-09", "04:20")


def test_a_document_with_no_mtime_is_skipped_rather_than_crashing():
    # `mtime` is read off the vault doc and a doc is free not to have one.
    # Without the guard this is a TypeError on None / 1000, which takes the
    # whole journal page down for one malformed file.
    from agora_runner.nova_journal import entry_times

    assert entry_times({JOURNAL_DIR + "093-cycle-86.md": None}) == {}


# The owner, capture 2026-08-24: "I want the time slot on the journals to be
# when they started, as it seems to show when they ended."
#
# He is reading it right, and it is the mtime rule above working as designed:
# a cycle writes its entry in its last few minutes, so the measured stamp is
# measured at the wrong end. The Agora conversation a cycle runs inside is
# created before the session opens, which is the other end and is measured too.


def test_the_card_shows_when_the_cycle_woke_not_when_it_filed():
    from agora_runner.nova_journal import entry_times, with_start_times

    # Cycle 381 woke at 20:40 Oslo and filed at 20:54.
    times = entry_times({JOURNAL_DIR + "437-cycle-381.md": 1787597640000})
    assert times == {381: [("2026-08-24", "20:54")]}

    stamps = with_start_times(times, {381: "2026-08-24T18:40:09.019Z"})
    entry = parse_journal(
        "### 2026-08-24 20:54 (Oslo) — Cycle 381 — A number of its own\n\nProse.",
        stamps,
    )[0]
    assert (entry["date"], entry["time"]) == ("2026-08-24", "20:40")


def test_a_cycle_with_no_conversation_keeps_the_write_time():
    # Conversations are the owner's to delete, and the archive reaches back
    # further than Agora's list does. Losing a start time must cost the card
    # its precision, never its stamp.
    from agora_runner.nova_journal import with_start_times

    times = {86: [("2026-08-10", "19:06")]}
    assert with_start_times(times, {381: "2026-08-24T18:40:09.019Z"}) == times
    assert with_start_times(times, {}) == times


def test_an_unparseable_created_at_keeps_the_write_time():
    from agora_runner.nova_journal import with_start_times

    times = {86: [("2026-08-10", "19:06")]}
    assert with_start_times(times, {86: "not a timestamp"}) == times
    assert with_start_times(times, {86: None}) == times


def test_a_naive_created_at_is_read_as_utc_and_shown_in_oslo():
    # Agora stamps UTC with a `Z`; every other reader of these fields in the
    # package assumes UTC when the offset is missing, and disagreeing here
    # would move a card by two hours rather than by the fourteen minutes
    # this change is about.
    from agora_runner.nova_journal import with_start_times

    assert with_start_times(
        {381: [("2026-08-24", "20:54")]}, {381: "2026-08-24T18:40:09"}
    ) == {381: [("2026-08-24", "20:40")]}


def test_both_entries_of_a_cycle_that_wrote_twice_take_the_one_wake():
    # Two documents, two write times, one run -- and the run woke once. The
    # list keeps its length so `parse_journal`'s nth-entry indexing is
    # untouched.
    from agora_runner.nova_journal import with_start_times

    times = {81: [("2026-08-10", "14:48"), ("2026-08-10", "14:46")]}
    assert with_start_times(times, {81: "2026-08-10T12:10:00Z"}) == {
        81: [("2026-08-10", "14:10"), ("2026-08-10", "14:10")],
    }


def test_a_conversation_for_a_cycle_that_wrote_nothing_adds_no_entry():
    # `journal_payload` reads `times.keys()` as the answer to "which cycles
    # wrote an entry" and builds the missing-cycle list from it. A run that
    # wrote nothing has a conversation and must not gain a card.
    from agora_runner.nova_journal import with_start_times

    stamps = with_start_times(
        {381: [("2026-08-24", "20:54")]},
        {379: "2026-08-24T17:40:07.030Z", 381: "2026-08-24T18:40:09.019Z"},
    )
    assert set(stamps) == {381}


def test_api_journal_serves_the_start_time_end_to_end():
    """The one test that pins the wiring rather than the arithmetic.

    Everything above tests `with_start_times` directly. `journal_payload` is
    where it is actually called, and the tests around it hand the shim an
    empty mtime map -- so with the seam deleted they all still pass, which is
    the whole failure shape this repo keeps re-finding. This one supplies
    real mtimes, so the write time is what the page shows unless the start
    time reaches it."""
    markdown = (
        "### 2026-08-24 20:54 (Oslo) — Cycle 381 — A number of its own\n\n"
        "Prose.\n\n---\nPR: #335 | Outcome: merged\n"
    )
    path = JOURNAL_DIR + "437-cycle-381.md"

    def _bulk(prefix, with_mtimes=False):
        files = VaultFiles({path: markdown} if prefix == JOURNAL_DIR else {})
        # 2026-08-24 20:54 Oslo -- when this cycle filed.
        return (files, {path: 1787597640000}) if with_mtimes else files

    def _read(_path):
        return None

    def _time_on_the_card():
        nova_site.reset_cache()
        with patch.object(nova_sources, "vault_bulk_fetch", side_effect=_bulk), \
                patch.object(nova_sources, "vault_read_path", side_effect=_read):
            status, _, body = _get("/api/journal")
        assert status == 200
        entries = json.loads(body)["entries"]
        assert len(entries) == 1
        return entries[0]["time"]

    with patch.object(nova_site, "cycle_starts", return_value={}):
        assert _time_on_the_card() == "20:54", "no start time: the write time stands"

    # 18:40:09Z -- when the heartbeat opened this cycle's conversation.
    with patch.object(
        nova_site, "cycle_starts", return_value={381: "2026-08-24T18:40:09.019Z"}
    ):
        assert _time_on_the_card() == "20:40"

    nova_site.reset_cache()


# The owner, issues.md 2026-08-10: "Nova takes a long time to load when i
# refresh it." /api/journal cost 3.0-3.5s on the live pod and was rebuilt,
# identically, on every request. It is served from cache now -- and none of
# that was pinned by a test when it shipped.


def test_the_journal_is_served_from_cache_rather_than_rebuilt(journal_md):
    """The second reader gets the first reader's build, immediately."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md) as read:
        _get("/api/journal")
        first = read.call_count
        assert first, "the first request builds"
        for _ in range(5):
            status, _, body = _get("/api/journal")
            assert status == 200
        assert read.call_count == first, "the vault was read again for an identical answer"
    assert json.loads(body)["entries"], "and the cached answer is the real one"


def test_a_stale_payload_is_refreshed_behind_the_request_that_got_it(journal_md):
    """Stale-while-revalidate: the reader is never the one who waits.

    The refresh runs on its own thread, so this asserts the vault was read
    again *after* a served response, not that the response itself was slow.
    """
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md) as read:
        _get("/api/journal")
        served = read.call_count
        with patch.object(nova_site, "CACHE_FRESH_SECONDS", 0):
            _get("/api/journal")
        for _ in range(50):
            if read.call_count > served:
                break
            time.sleep(0.05)
    assert read.call_count > served, "a stale payload was served and never refreshed"


# The owner, issues.md #71: "I takes 6-7 seconds to load the Nova app, even
# though only 20 journals are shown." The cache above fixed the *second*
# load and left the first one alone -- and this process is new most hours,
# because a cycle merging into the runner rolls the nova-site pod. Measured
# on the live pod 2026-08-12, 26 minutes after the #125 deploy with nothing
# having visited since: /api/journal?limit=20 answered in 5.70s, then 0.009s.


def test_the_first_visit_after_a_deploy_is_served_from_a_warmed_cache(journal_md):
    """The visitor who arrives into a fresh process pays nothing.

    Asserted as "the vault was not read again" rather than as a duration:
    the 5.7s is the vault fetch plus the parse, and a wall-clock assertion
    on a test fixture would be measuring neither.
    """
    nova_site.reset_cache()
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md) as read:
        nova_site.warm_cache()
        warmed = read.call_count
        assert warmed, "the warm built nothing at all"
        status, _, body = _get("/api/journal")
        assert read.call_count == warmed, "the first visitor paid the cold build anyway"
    assert status == 200
    assert json.loads(body)["entries"], "and what was warmed is the real answer"


def test_the_warm_does_not_hold_up_the_server_it_runs_behind(journal_md):
    """Started after the socket is being served, and never waited on.

    A warm that ran inline would be six seconds of a pod that is listening
    to nobody -- long enough for the readiness probe to call it dead. So
    this blocks inside the warm and asserts `start_nova_site` came back
    while it was still in there; called synchronously, the call below would
    not return until this test's own timeout expired.
    """
    entered = threading.Event()
    finished = threading.Event()
    release = threading.Event()

    def blocking_warm():
        entered.set()
        release.wait(10)
        finished.set()

    with patch.object(nova_site, "warm_cache", side_effect=blocking_warm), \
            patch.object(nova_site, "NOVA_PORT", 0):
        server = nova_site.start_nova_site()
    try:
        assert entered.wait(10), "start_nova_site never warmed the cache"
        assert not finished.is_set(), "the warm ran on the startup path"
        assert server.server_address[1] != 0, "and the socket was bound before it"
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_a_warm_that_cannot_reach_the_vault_costs_only_the_warm():
    """CouchDB refusing at startup must not take down a daemon thread, and
    must not stop the payloads behind the failing one from being built."""
    nova_site.reset_cache()
    attempted = []

    def couch_is_down(name, build):
        attempted.append(name)
        if name == "journal":
            raise RuntimeError("couch is down")
        return ({}, "{}", 'W/"x"')

    with patch.object(nova_site, "cached_payload", side_effect=couch_is_down):
        nova_site.warm_cache()
    # Literals rather than a comparison against WARM_PAYLOADS itself: that
    # list is the code under test, and a test that reads it back agrees
    # with whatever it says. A third payload added here should fail this
    # and be looked at.
    assert attempted == ["journal", "digest"], "the payload behind the failing one was skipped"
    assert "journal" not in nova_site._cache, "a failed build must not be cached"


def test_nothing_is_warmed_that_the_request_path_will_not_read_back():
    """A warmed payload no handler reads from the cache is a vault round
    trip at every process start that nobody can ever collect.

    The obvious version of `WARM_PAYLOADS` is the three requests `app.js`
    makes on a cold load, and the reviewer caught that being wrong:
    `/api/comments` is deliberately *not* served through `cached_payload`,
    because it changes underneath itself. So the list has to agree with
    the handlers rather than with the client, and this reads the handlers
    to check -- an assertion restating the list would move with it.
    """
    served = set()
    for node in ast.walk(ast.parse(inspect.getsource(nova_site))):
        if not isinstance(node, ast.Call):
            continue
        named = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if named not in ("cached_payload", "cached_entry", "_send_cached_json"):
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            served.add(first.value)
    # A scanner that finds nothing agrees with any list at all, which is
    # the shape of vacuous guard this suite already bans elsewhere.
    assert "journal" in served and "digest" in served, (
        f"the handler scan found {sorted(served)} -- it is no longer reading the request path"
    )
    warmed = {name for name, _ in nova_site.WARM_PAYLOADS}
    assert warmed <= served, (
        f"{sorted(warmed - served)} is built at startup and no handler reads it back"
    )


def test_an_unchanged_journal_answers_a_returning_client_with_304(journal_md):
    """The page polls every 30 seconds. Answering "nothing new" with 160KB
    is what makes polling expensive enough to talk yourself out of."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, head, body = _get("/api/journal")
        etag = re.search(r"ETag: (\S+)", head).group(1)
        assert json.loads(body)["version"] == etag, "the client cannot read the header from a SW cache"

        status, head, body = _get("/api/journal", f"If-None-Match: {etag}\r\n")
    assert status == 304
    assert body == b""
    assert "Vary: Accept-Encoding" in head


def test_a_journal_that_changed_gets_a_new_version(journal_md):
    """The client re-renders on this string and nothing else, so an etag
    that does not move is a page that never updates."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, head, _ = _get("/api/journal")
    first = re.search(r"ETag: (\S+)", head).group(1)
    nova_site.reset_cache()
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md + "\n\n### Cycle 999 (2026-08-10 21:00)\n\nA new entry.\n\n---\nPR: none | Outcome: no-op\n"):
        _, head, _ = _get("/api/journal")
    assert re.search(r"ETag: (\S+)", head).group(1) != first


# The cold load, which #84's conditional poll deliberately did not touch:
# every entry ever written -- 109 of them, 678,027 bytes raw and 187,148
# gzipped off the live pod at 06:11 Oslo on 2026-08-11, one more an hour.
# The fixture is five entries -- cycles 49, 29, 19, 6, and one with no cycle
# number at all -- so a limit of 2 is a real page and a limit of 99 is not.


def test_a_limit_serves_the_newest_entries_and_says_how_many_there_are(journal_md):
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        status, _, body = _get("/api/journal?limit=2")
    assert status == 200
    payload = json.loads(body)
    assert [e["cycle"] for e in payload["entries"]] == [49, 29], "not the newest two, in order"
    assert payload["total"] == 5, "the pager cannot know there is more without this"


def test_an_offset_walks_further_back_without_repeating_a_page(journal_md):
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, _, first = _get("/api/journal?limit=2")
        _, _, second = _get("/api/journal?limit=2&offset=2")
    seen = [e["cycle"] for e in json.loads(first)["entries"]]
    seen += [e["cycle"] for e in json.loads(second)["entries"]]
    assert seen == [49, 29, 19, 6]


def test_asking_for_no_limit_still_serves_the_whole_journal(journal_md):
    """An app.js served out of a service worker's cache from before this
    shipped sends no `limit`, and must not silently lose the feed."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, _, body = _get("/api/journal")
    assert len(json.loads(body)["entries"]) == 5


def test_a_deep_linked_cycle_is_served_without_paging_back_to_it(journal_md):
    """`/cycle/6` is older than any first page it will ever be on."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, _, body = _get("/api/journal?cycle=6")
    payload = json.loads(body)
    assert [e["cycle"] for e in payload["entries"]] == [6]
    assert payload["total"] == 5, "the whole-corpus count is still the whole corpus"


def test_the_status_header_is_the_whole_corpus_not_the_page(journal_md):
    """`status` is what the header renders, and it is computed over every
    entry -- a page of one must not make it describe one.

    Asserted against the corpus figures rather than against the unpaginated
    response: the fixture's newest entry is the newest on every page, so
    comparing the two responses to each other passes just as happily when
    `status` is rebuilt from the slice. It did, the first time this was
    written.
    """
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, _, page = _get("/api/journal?limit=1")
    status = json.loads(page)["status"]
    assert len(json.loads(page)["entries"]) == 1
    assert status["entryCount"] == 5
    assert status["runningDays"] == 7


def test_each_window_has_its_own_version(journal_md):
    """A client that has just asked for four entries must not be handed a
    304 against the two it already had."""
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        _, head, body = _get("/api/journal?limit=2")
        small = re.search(r"ETag: (\S+)", head).group(1)
        assert json.loads(body)["version"] == small
        _, head, _ = _get("/api/journal?limit=4")
        large = re.search(r"ETag: (\S+)", head).group(1)
        assert small != large

        status, _, _ = _get("/api/journal?limit=2", f"If-None-Match: {small}\r\n")
        assert status == 304, "an unchanged window is still a 304"
        status, _, _ = _get("/api/journal?limit=4", f"If-None-Match: {small}\r\n")
    assert status == 200, "a wider window was answered as if nothing had changed"


def test_a_junk_window_is_the_default_rather_than_a_500(journal_md):
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        for query in ["?limit=nonsense", "?limit=-3", "?offset=-1", "?limit=", "?cycle=x"]:
            status, _, body = _get("/api/journal" + query)
            assert status == 200, query
            assert json.loads(body)["entries"], query


def test_a_limit_past_the_end_of_the_journal_is_not_an_error(journal_md):
    with patch.object(nova_sources, "vault_read_path", return_value=journal_md):
        status, _, body = _get("/api/journal?limit=500&offset=0")
    assert status == 200
    assert len(json.loads(body)["entries"]) == 5


def test_a_window_never_cuts_a_cycle_in_half(journal_md):
    """Six cycles wrote a second entry, and the client gives a cycle's
    digest line to whichever of its entries it can see furthest back. Split
    a pair across the page boundary and the summary renders on the addendum
    -- then jumps to the real entry the moment the window grows past it.

    The fixture has no addendum, so this builds one: two entries for cycle
    49 with a boundary deliberately between them.
    """
    doubled = journal_md.replace(
        "### 2026-08-09 04:20 (Oslo) — Cycle 49",
        "### 2026-08-09 05:00 (Oslo) — Cycle 49\n\nAn addendum.\n\n---\nPR: none | Outcome: no-op\n\n"
        "### 2026-08-09 04:20 (Oslo) — Cycle 49",
        1,
    )
    with patch.object(nova_sources, "vault_read_path", return_value=doubled):
        _, _, body = _get("/api/journal?limit=1")
    served = [e["cycle"] for e in json.loads(body)["entries"]]
    assert served == [49, 49], f"the boundary split cycle 49: {served}"


# --- the digest window ------------------------------------------------------


def _both(journal_md, digest_md):
    """A vault where the journal and the digest are different files.

    Every test above patches `vault_read_path` with one `return_value`, so
    whatever it asks for it gets the same markdown back. The digest window
    is the first thing that reads both in one request -- it resolves its
    cycle range by asking `journal_page` -- so it needs a vault that can
    tell them apart.

    Three files now, not two: the archive has to answer as an empty
    archive rather than fall through to `journal_md`. Without that, every
    test below feeds a `###`-headed journal fixture to `digest_markdown`
    as the digest archive, and they all still pass -- but only because
    `journal_sample.md` happens to contain nothing matching
    `_DIGEST_LINE_RE`, which is a coincidence of that fixture's contents
    and not a thing any of them assert.
    """
    from agora_runner.nova_journal import DIGEST_ARCHIVE_PATH, DIGEST_PATH

    def read(path):
        if path == DIGEST_PATH:
            return digest_md
        if path == DIGEST_ARCHIVE_PATH:
            return ""
        return journal_md

    return patch.object(nova_sources, "vault_read_path", side_effect=read)


def test_the_window_fixtures_do_not_feed_the_journal_in_as_the_archive(journal_md, digest_md):
    """`_both` means three files, and this is what says so.

    Without it the helper's correction is unpinned: reverting the archive
    branch puts a `###`-headed journal fixture into `digest_markdown` as
    the archive, and all ten tests below go on passing, because
    `journal_sample.md` happens to hold nothing `_DIGEST_LINE_RE` matches.
    Asserted against the digest fixture as a literal rather than against
    a second call, so there is nothing a mutation can move on both sides.
    """
    with _both(journal_md, digest_md):
        assert nova_sources.digest_markdown() == digest_md


def test_the_digest_window_is_the_cycles_the_feed_is_showing(journal_md, digest_md):
    """The fixtures are the live files: 5 journal entries (cycles 49, 29,
    19, 6 and one with no number) against 11 digest lines (49 down to 38).

    Asserted as literals rather than against another response, because a
    slice compared to the endpoint that produced it can survive its own
    mutation -- Cycle 101 shipped one that did. One entry is cycle 49
    alone, so exactly one line comes back; two entries reach back to cycle
    29, and every line in the file is inside that range. **A line-count
    slice would have returned two.** That is the difference this test is
    here to catch.
    """
    with _both(journal_md, digest_md):
        _, _, one = _get("/api/digest?limit=1")
        nova_site.reset_cache()
        _, _, two = _get("/api/digest?limit=2")
    assert [line["cycle"] for line in json.loads(one)["lines"]] == [49]
    assert [line["cycle"] for line in json.loads(two)["lines"]] == [
        49, 48, 47, 46, 44, 43, 42, 41, 40, 39, 38
    ]


def test_the_digest_says_how_many_lines_there_are_in_all(journal_md, digest_md):
    """`totalLines` is the whole file, not the window -- the count has to
    survive the slice or nothing can tell a short digest from a cut one."""
    with _both(journal_md, digest_md):
        _, _, body = _get("/api/digest?limit=1")
    payload = json.loads(body)
    assert payload["totalLines"] == 11
    assert len(payload["lines"]) == 1


def test_every_cycle_on_the_page_still_has_its_summary(journal_md, digest_md):
    """The alignment the window exists to keep. Whatever the feed shows,
    every one of those cycles that has a digest line must have been sent
    it -- otherwise a card silently loses its summary at the boundary."""
    with _both(journal_md, digest_md):
        _, _, feed = _get("/api/journal?limit=2")
        nova_site.reset_cache()
        _, _, digest = _get("/api/digest?limit=2")
    shown = {e["cycle"] for e in json.loads(feed)["entries"] if e["cycle"] is not None}
    sent = {line["cycle"] for line in json.loads(digest)["lines"]}
    have_a_line = {49, 48, 47, 46, 44, 43, 42, 41, 40, 39, 38}
    assert shown & have_a_line, "the fixture window has no summarised cycle to lose"
    assert (shown & have_a_line) <= sent


def test_an_addendum_does_not_lose_its_cycles_summary_at_the_boundary(journal_md, digest_md):
    """The trap the whole design is shaped around, and the reason the
    window is a cycle range rather than an offset.

    Cycle 49 gets a second entry, so a one-entry window grows to two to
    avoid splitting it -- and the digest has to grow with it. Slicing the
    digest at the journal's *offset* would have cut after the first entry
    and dropped the line for the cycle straddling the boundary.
    """
    doubled = journal_md.replace(
        "### 2026-08-09 04:20 (Oslo) — Cycle 49",
        "### 2026-08-09 05:00 (Oslo) — Cycle 49\n\nAn addendum.\n\n---\nPR: none | Outcome: no-op\n\n"
        "### 2026-08-09 04:20 (Oslo) — Cycle 49",
        1,
    )
    with _both(doubled, digest_md):
        _, _, feed = _get("/api/journal?limit=1")
        nova_site.reset_cache()
        _, _, digest = _get("/api/digest?limit=1")
    assert [e["cycle"] for e in json.loads(feed)["entries"]] == [49, 49]
    assert [line["cycle"] for line in json.loads(digest)["lines"]] == [49]


def test_a_deep_linked_cycle_gets_its_own_line_and_no_others(journal_md, digest_md):
    with _both(journal_md, digest_md):
        _, _, body = _get("/api/digest?cycle=44")
    assert [line["cycle"] for line in json.loads(body)["lines"]] == [44]


def test_a_digest_asked_for_the_old_way_is_still_the_whole_digest(journal_md, digest_md):
    """An app.js out of a service worker's cache from before this shipped
    sends no window, and must not silently lose ten of eleven summaries."""
    with _both(journal_md, digest_md):
        _, _, body = _get("/api/digest")
    assert len(json.loads(body)["lines"]) == 11


def test_the_handoff_section_survives_every_window(journal_md, digest_md):
    """`nextCycle` is the header, not the feed -- it is not part of what
    gets sliced, and it renders on every window the same way the status
    header does. `needsEdvard` used to be asserted here too; the block was  (not-prose: an identifier)
    deleted from the page in #229 and its server half in #236."""
    with _both(journal_md, digest_md):
        _, _, windowed = _get("/api/digest?limit=1")
        nova_site.reset_cache()
        _, _, whole = _get("/api/digest")
    windowed, whole = json.loads(windowed), json.loads(whole)
    assert windowed["nextCycle"] == whole["nextCycle"]


def test_each_digest_window_has_its_own_version(journal_md, digest_md):
    """Same reason the journal's windows do: a client that has just asked
    for a wider window must not be told it already has it."""
    with _both(journal_md, digest_md):
        _, head, body = _get("/api/digest?limit=1")
        small = re.search(r"ETag: (\S+)", head).group(1)
        assert json.loads(body)["version"] == small
        _, head, _ = _get("/api/digest?limit=2")
        assert re.search(r"ETag: (\S+)", head).group(1) != small
        status, _, _ = _get("/api/digest?limit=1", f"If-None-Match: {small}\r\n")
        assert status == 304
        status, _, _ = _get("/api/digest?limit=2", f"If-None-Match: {small}\r\n")
        assert status == 200


def test_a_digest_line_no_longer_carries_its_text_three_times(digest_md):
    """`text` once, and the two drawers that between them are the same text
    again -- #61 stopped reading the third copy and it kept being sent.
    Measured on the live pod at 07:03 Oslo 2026-08-11: `lines` was 266,393
    of the endpoint's 270,793 bytes."""
    line = parse_digest(digest_md)["lines"][0]
    assert "spans" not in line
    assert line["briefSpans"]


def test_a_cold_load_builds_the_journal_once_not_once_per_request():
    """`/api/journal` and `/api/digest` are asked for together and both
    want the journal payload, on a ThreadingHTTPServer. The build is
    3.0-3.5s of vault fetch and parse against the live pod, and paying it
    twice concurrently for one answer is what a cold load would have cost
    without the lock."""
    builds = []

    def slow_build():
        builds.append(1)
        time.sleep(0.05)
        return {"entries": []}

    import threading

    nova_site.reset_cache()
    threads = [
        threading.Thread(target=lambda: nova_site.cached_payload("j", slow_build))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(builds) == 1, f"the cold build ran {len(builds)} times"


def test_the_digest_window_revalidates_when_the_journal_moves(journal_md, digest_md):
    """The digest's window is resolved out of the journal, so the right
    answer can change while the digest file does not.

    Cycle 19 gets an addendum. The two-entry window is now 49 and the two
    halves of 19, so it reaches back past 29 -- a different cycle range
    over a byte-identical digest. Keyed on the digest alone the poll would
    be told 304, and the card that just came into view would render with
    no summary until the next digest write.
    """
    with_addendum = journal_md.replace(
        "### 2026-08-04 — Cycle 19 (Nova)",
        "### 2026-08-04 — Cycle 19 (Nova)\n\nAn addendum.\n\n---\nPR: none | Outcome: no-op\n\n"
        "### 2026-08-04 — Cycle 19 (Nova)",
        1,
    )
    assert with_addendum != journal_md, "the fixture heading moved"
    with _both(journal_md, digest_md):
        _, head, _ = _get("/api/digest?limit=2")
        before = re.search(r"ETag: (\S+)", head).group(1)
    nova_site.reset_cache()
    with _both(with_addendum, digest_md):
        _, head, _ = _get("/api/digest?limit=2")
        after = re.search(r"ETag: (\S+)", head).group(1)
        status, _, _ = _get("/api/digest?limit=2", f"If-None-Match: {before}\r\n")
    assert after != before, "the same etag for a different window"
    assert status == 200, "a moved window was answered 304"


def test_the_costs_page_and_its_endpoint_both_answer():
    """`/costs` is a shell route and `/api/costs` is the data behind it,
    and nothing else in this suite would notice either one disappearing:
    `nova_costs`'s own tests call the shaping directly, and the browser
    tests stub `fetch`. So a route refactor could drop the pair and leave
    1391 tests green while the nav tab 404s on his phone.

    The shell is asserted by content rather than by status, because a 404
    here is also a 200 -- `_send_json(404, ...)` -- and only the body
    tells the two apart."""
    ledger = open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                     "cost_ledger_sample.json"),
        encoding="utf-8",
    ).read()
    with patch.object(nova_sources, "vault_read_path", return_value=ledger):
        nova_site.reset_cache()
        status, _, body = _get("/api/costs")
        shell_status, _, shell = _get("/costs")
    assert status == 200
    assert json.loads(body)["summary"]["cycles"] == 3
    assert len(json.loads(body)["cycles"]) == 3
    assert shell_status == 200 and b"<!doctype html>" in shell.lower()


def test_the_retro_page_and_its_endpoint_both_answer():
    """The same pair, for the same reason: `nova_retro`'s own tests call
    the shaping directly and the browser tests stub `fetch`, so nothing
    else here would notice `/retro` or `/api/retro` disappearing and the
    nav tab 404ing on his phone.

    The empty case is asserted alongside the full one because it is the
    state this ships in -- the first retrospective has not run yet, and a
    502 on a nav tab from the day it appears until Friday morning is the
    realistic failure, not a malformed ledger."""
    ledger = json.dumps({"retros": [{
        "date": "2026-08-14", "cycle": 181,
        "scores": {"going": 7, "effectiveness": 6, "feeling": 8},
        "overall": "Steady.", "good": "Ships.", "bad": "Repeats itself.", "changes": [],
    }]})
    with patch.object(nova_sources, "vault_read_path", return_value=ledger):
        nova_site.reset_cache()
        status, _, body = _get("/api/retro")
        shell_status, _, shell = _get("/retro")
    assert status == 200
    assert json.loads(body)["retros"][0]["scores"]["feeling"] == 8
    assert shell_status == 200 and b"<!doctype html>" in shell.lower()

    with patch.object(nova_sources, "vault_read_path", return_value=None):
        nova_site.reset_cache()
        empty_status, _, empty = _get("/api/retro")
    assert empty_status == 200
    assert json.loads(empty)["retros"] == []


# --- /api/health -------------------------------------------------------
#
# The endpoint exists because verifying a database flip used to cost a
# write probe: append a note to a board, poll `/api/board` until the count
# moves, and outlast a 15-second cache that hands back the pre-write number
# four times running. Cycle 121 did that and nearly recorded the migration
# as failed. These tests pin the two questions it answers separately --
# what did this process *resolve*, and what can it actually *reach* --
# because during a migration those two disagree and that gap is the risk.


def _couch_stub(reachable):
    """Stand in for `couch_req` on a `GET <dbname>`. `reachable` maps a
    database name to its doc_count, or to None for a database that is
    named in config but does not answer.

    Takes `timeout` because `database_health` passes a short one, and
    `database_health` catches every exception to report it as an
    unreachable database -- so a stub whose signature has drifted from the
    caller does not fail as a TypeError, it quietly reports CouchDB as
    down. Keep this signature in step with `couch_req`."""
    def fake(method, path, body=None, timeout=None):
        name = urllib.parse.unquote(path)
        count = reachable.get(name)
        if count is None:
            return 404, {"error": "not_found"}
        return 200, {"db_name": name, "doc_count": count}
    return fake


def test_health_reports_both_databases_and_their_counts():
    with patch.object(vault, "COUCHDB_NOVA_DB", "nova"), \
         patch.object(vault, "couch_req", _couch_stub({"obsidian": 12096, "nova": 900})):
        status, _, body = _get("/api/health")
    payload = json.loads(body)
    assert status == 200 and payload["ok"] is True
    assert payload["routing_enabled"] is True
    assert payload["databases"]["main"] == {
        "name": "obsidian", "reachable": True, "doc_count": 12096, "error": None,
    }
    assert payload["databases"]["nova"] == {
        "name": "nova", "reachable": True, "doc_count": 900, "error": None,
    }


def test_health_is_503_when_a_named_database_does_not_answer():
    """The migration failure mode with nothing else to see it: the config
    names `nova`, every route resolves to `nova`, and `nova` is not there.
    Routing looks perfectly healthy on its own -- only reachability
    distinguishes a working flip from a flip into a void."""
    with patch.object(vault, "COUCHDB_NOVA_DB", "nova"), \
         patch.object(vault, "couch_req", _couch_stub({"obsidian": 12096})):
        status, _, body = _get("/api/health")
    payload = json.loads(body)
    assert status == 503 and payload["ok"] is False
    assert payload["databases"]["nova"]["reachable"] is False
    assert payload["databases"]["nova"]["error"] == "HTTP 404"
    # The half that still works must still say so, or a reader cannot tell
    # "one database is down" from "CouchDB is down".
    assert payload["databases"]["main"]["reachable"] is True


def test_health_routes_pin_every_branch_of_the_routing_rule():
    with patch.object(vault, "COUCHDB_NOVA_DB", "nova"), \
         patch.object(vault, "couch_req", _couch_stub({"obsidian": 1, "nova": 1})):
        _, _, body = _get("/api/health")
    routes = {r["path"]: r["database"] for r in json.loads(body)["routes"]}
    assert routes["projects/sokrates/projects/agora/journal-digest.md"] == "nova"
    # A `.bak` beside the digest is the owner's file and must not follow it.
    assert routes["projects/sokrates/projects/agora/journal-digest.md.bak"] == "obsidian"
    # The Nova folder he asked to keep in his own vault.
    assert routes["projects/sokrates/projects/nova/nova.md"] == "obsidian"
    # A file he writes by hand. It sat under the agora folder until
    # 2026-08-12 and moved into the Nova folder in his own vault at his
    # ask -- the folder changed, the database deliberately did not.
    assert routes["projects/sokrates/projects/nova/issues.md"] == "obsidian"
    assert routes[
        "projects/sokrates/projects/agora/nova/journal/138-cycle-121.md"
    ] == "nova"


def test_health_reports_one_database_when_routing_is_off():
    """Unset `COUCHDB_NOVA_DB` is the pre-migration world, and the endpoint
    has to describe it honestly rather than report a `nova` that is not
    configured -- otherwise it cannot be used to confirm a rollback."""
    with patch.object(vault, "COUCHDB_NOVA_DB", ""), \
         patch.object(vault, "couch_req", _couch_stub({"obsidian": 12096})):
        status, _, body = _get("/api/health")
    payload = json.loads(body)
    assert status == 200 and payload["routing_enabled"] is False
    assert "nova" not in payload["databases"]
    assert {r["database"] for r in payload["routes"]} == {"obsidian"}


def test_health_is_never_cached():
    """The one property the endpoint exists for. A cached answer is worse
    than no endpoint, because it is confidently stale at exactly the
    moment someone is checking whether a flip took effect."""
    counts = iter([12096, 12097])
    def fake(method, path, body=None, timeout=None):
        return 200, {"doc_count": next(counts)}
    with patch.object(vault, "COUCHDB_NOVA_DB", ""), \
         patch.object(vault, "couch_req", fake):
        _, _, first = _get("/api/health")
        _, _, second = _get("/api/health")
    assert json.loads(first)["databases"]["main"]["doc_count"] == 12096
    assert json.loads(second)["databases"]["main"]["doc_count"] == 12097, \
        "the second call was served from cache"


def test_health_probes_use_a_short_timeout_not_the_60s_default():
    """`database_health` probes each named database in turn, so at the 60s
    default two unreachable ones cost two minutes -- the slow uncertain
    wait this endpoint exists to replace."""
    seen = []

    def fake(method, path, body=None, timeout=None):
        seen.append(timeout)
        return 200, {"doc_count": 1}

    with patch.object(vault, "COUCHDB_NOVA_DB", "nova"), \
         patch.object(vault, "couch_req", fake):
        _get("/api/health")
    assert seen == [vault.HEALTH_TIMEOUT_SECONDS] * 2
    assert vault.HEALTH_TIMEOUT_SECONDS < 60


# --- a capture is visible on the very next request -----------------------
#
# the owner, `issues.md` 2026-08-12: *"When i create a new issues, the 'not
# boarded yet' block for issues is not refreshed automatically. This is
# probably a problem for ideas aswell."*
#
# It was, for both, and deterministically: `cached_payload` always serves
# the entry it holds and rebuilds behind the request, so the reload
# `app.js` fires after a capture could only ever render the board from
# before the capture. Nothing here is timing-dependent, which is why the
# fixtures below need no clock control.


def _board_captures(name="issues"):
    """The capture bullets as plain strings.

    `/api/board` serves each one as rendered blocks, so the text has to be
    walked out of the spans -- comparing rendered structures would make
    these tests fail on a change to the renderer that has nothing to do
    with what they are pinning.

    The rendered blocks rather than the raw `text` beside them, still, and
    deliberately: `text` was added for edit and delete to address a bullet
    by (issues.md #66) and reading it here would stop these pinning that
    the *page* shows the capture, which is what they exist for."""
    status, _, body = _get(f"/api/board?name={name}")
    assert status == 200, status
    out = []
    for capture in json.loads(body)["captures"]:
        out.append("".join(
            span.get("text", "")
            for block in capture["blocks"]
            for span in block.get("spans", [])
        ))
    return out


def test_a_capture_is_in_the_board_on_the_very_next_request():
    """The bug, stated as the behaviour the owner expects."""
    nova_site.reset_cache()
    live = {"text": "---\n---\n\n- an older capture\n- \n\n## Board\n"}
    with patch.object(nova_site, "board_markdown",
                      side_effect=lambda name: (live["text"], "", "")):
        assert _board_captures() == ["an older capture"]

        def _write(target, text, priority=""):
            live["text"] = live["text"].replace(
                "- \n", f"- {text}\n- \n", 1
            )
            return True, "ok"

        with patch.object(nova_site, "capture", side_effect=_write):
            status, _, _ = _post("/api/capture",
                                 {"target": "issues", "text": "the new one"})
        assert status == 200
        assert _board_captures() == ["an older capture", "the new one"]


def test_capturing_an_idea_does_not_invalidate_the_issues_board():
    """The invalidation is keyed on the target, not a blanket cache drop:
    a cache cleared on every write is the 3.5s cold journal load back."""
    nova_site.reset_cache()
    with patch.object(nova_site, "board_markdown",
                      side_effect=lambda name: ("---\n---\n\n- x\n- \n\n## Board\n", "", "")):
        _board_captures("issues")
        with patch.object(nova_site, "capture", return_value=(True, "ok")):
            _post("/api/capture", {"target": "ideas", "text": "y"})
    assert "board:issues" in nova_site._cache
    assert "board:ideas" not in nova_site._cache


def test_a_failed_capture_leaves_the_cache_alone():
    """Nothing was written, so making the next reader pay a cold build
    buys nothing."""
    nova_site.reset_cache()
    with patch.object(nova_site, "board_markdown",
                      side_effect=lambda name: ("---\n---\n\n- x\n- \n\n## Board\n", "", "")):
        _board_captures("issues")
        with patch.object(nova_site, "capture", return_value=(False, "nope")):
            _post("/api/capture", {"target": "issues", "text": "y"})
    assert "board:issues" in nova_site._cache


def test_a_refresh_already_running_cannot_put_the_stale_answer_back():
    """The race the generation counter exists for.

    A background rebuild that started *before* the capture read the vault
    before the write landed. Left alone it would finish afterwards and
    store the pre-capture payload into the cache `invalidate` had just
    emptied -- reinstating the exact bug, and only under load, which is
    the worst way to find it.
    """
    nova_site.reset_cache()
    nova_site._cache["board:issues"] = ({"captures": ["stale"]}, "{}", 'W/"x"', 0.0)
    started = nova_site._refresh_started_probe = None

    def _slow_build():
        # Stands in for a rebuild in flight: the invalidation lands while
        # this is running, exactly as a real capture would.
        nova_site.invalidate("board:issues")
        return {"captures": ["read before the write"]}

    nova_site._refresh("board:issues", _slow_build)
    assert "board:issues" not in nova_site._cache, (
        "a build that read the vault before the write must not repopulate"
    )
    assert started is None


def test_only_the_window_the_reader_asked_for_is_ever_rendered():
    """The cost this removes, pinned by counting the renders.

    `journal_payload` used to build blocks for every entry so that
    `journal_page` could slice out twenty -- 158 entries and 1.07MB per
    process, growing by one an hour. A change that quietly renders eagerly
    again looks identical from the outside: same JSON, same etag, same
    tests green. So count. Two entries in the window, two renders, and the
    build itself renders nothing at all.
    """
    with patch.object(nova_sources, "vault_read_path", return_value=_fixture("journal_sample.md")):
        with patch.object(nova_site, "render_blocks", wraps=nova_site.render_blocks) as render:
            payload = nova_site.journal_payload()
            assert len(payload["entries"]) > 2, "fixture must be wider than the window"
            assert render.call_count == 0

            page = nova_site.journal_page(payload, limit=2, offset=0)
            assert render.call_count == len(page["entries"])
            assert page["entries"][0]["blocks"]
            assert "body" not in page["entries"][0]

            # And a second reader of the same window pays nothing: the
            # blocks stay on the cached entry, which is what makes this a
            # deferral rather than a move of the same work onto the request.
            nova_site.journal_page(payload, limit=2, offset=0)
            assert render.call_count == len(page["entries"])


# --- The eight-cycle report card (the owner, comments board at cycle 156) ---


REPORT_ENTRY = (
    "### 2026-08-13 14:00 (Oslo) — Report · Cycles 149–156\n\n"
    "Eight hours in plain language.\n\n"
    "PR: none | Outcome: report\n"
)


def test_a_report_heading_is_parsed_as_a_report():
    entry = nova_journal.parse_heading(
        "2026-08-13 14:00 (Oslo) — Report · Cycles 149–156"
    )
    assert entry["kind"] == "report"
    assert entry["cycle"] is None
    assert entry["title"] == "Report · Cycles 149–156"


def test_an_ordinary_cycle_heading_is_not_a_report():
    entry = nova_journal.parse_heading("2026-08-13 07:20 (Oslo) — Cycle 157")
    assert entry["kind"] == "cycle"


def test_an_entry_merely_titled_report_is_still_a_cycle_card():
    """The declaration is the whole segment, not a word inside it.

    A cycle whose own entry is about the reports -- which is exactly what
    the cycle that built them wrote -- must not turn its card purple. This
    is the guard that makes reading the marker off a heading defensible at
    all, so it is tested rather than assumed.
    """
    for heading in (
        "2026-08-13 07:20 (Oslo) — Cycle 157 — Report · Cycles 149–156",
        "2026-08-13 07:20 (Oslo) — Report on the last eight cycles",
        "2026-08-13 07:20 (Oslo) — A Report · Cycles 149–156 card",
        # The trailing anchor specifically. The three above all fail on the
        # leading one, which `.match` supplies anyway -- so without this
        # line the shape could be unanchored at the end and every test here
        # would still pass. Found by mutation, not by reading it.
        "2026-08-13 07:20 (Oslo) — Report · Cycles 149–156 and what broke",
    ):
        assert nova_journal.parse_heading(heading)["kind"] == "cycle", heading


def test_a_report_card_gets_the_report_emoji_not_a_scored_one():
    """A report quotes eight cycles' topics, so the scorer would pick one of
    them at random. Two reports with different subject matter must still
    carry the same emoji."""
    entries = nova_journal.parse_journal(
        REPORT_ENTRY
        + "\n### 2026-08-13 06:00 (Oslo) — Report · Cycles 141–148\n\n"
        + "The vault and the heartbeat and the pods and the quota.\n\n"
        + "PR: none | Outcome: report\n"
    )
    assert [e["kind"] for e in entries] == ["report", "report"]
    assert {e["emoji"] for e in entries} == {nova_journal.REPORT_EMOJI}


def test_a_report_entry_still_carries_its_outcome_and_body():
    """It is an ordinary journal document in every other respect -- it goes
    through `lint_entry`, it gets a badge, and it has a brief."""
    entry = nova_journal.parse_journal(REPORT_ENTRY)[0]
    assert entry["outcome"] == "report"
    assert entry["pr"] == "none"
    assert "Eight hours in plain language." in entry["body"]


def test_a_report_cards_brief_is_its_summary_and_not_its_label():
    """The wiring, not the helper. Every other test of this calls
    `strip_brief_label` directly, so removing its one call site in
    `parse_journal` left the whole suite green -- the reviewer on runner#204
    found that by reverting the line. This is the test that fails instead.

    The body is the real shape: report 242 opens `**TL;DR.**` followed by the
    summary in the same paragraph, which is what makes the label the whole
    brief without the strip."""
    document = (
        "### 2026-08-13 14:00 (Oslo) — Report · Cycles 149–156\n\n"
        "**TL;DR.** These eight hours went on two things. "
        "Your boards now say what has actually shipped.\n\n"
        "PR: none | Outcome: report\n"
    )
    entry = nova_journal.parse_journal(document)[0]
    brief = "".join(span["text"] for span in entry["briefSpans"])
    assert brief.startswith("These eight hours went on two things.")
    assert "TL;DR" not in brief
    # The label is dropped from the card's brief, not from the report.
    assert "**TL;DR.**" in entry["body"]


def test_the_server_routes_pages_off_the_shared_constant():
    """`PAGE_ROUTES` must be what `do_GET` reads, not a description of it.

    `site_check` walks this constant to decide which pages to smoke-test,
    so the constant only buys anything if the server and the checker
    cannot disagree. Re-hardcoding the list inside `do_GET` -- which is
    where it lived until this landed -- leaves every other test in this
    file green, because the behaviour for today's five routes is
    identical. The only thing that catches it is asking the server about a
    route that exists solely in the constant.
    """
    with patch.object(nova_site, "PAGE_ROUTES", nova_site.PAGE_ROUTES + ("/invented",)):
        status, _, body = _get("/invented")
    assert status == 200 and b"<!doctype html>" in body.lower(), (
        "the server is not reading PAGE_ROUTES, so site_check is walking a list "
        "that no longer describes what is routed"
    )
    # And with the patch gone it is a 404 again, so the assertion above is
    # about the constant rather than about a server that serves anything.
    assert _get("/invented")[0] == 404


def test_nova_site_main_is_runnable_as_a_module():
    """`python -m agora_runner.nova_site_main` must start a server, not exit 0.

    Without the `__main__` guard the module imports, defines `main`, calls
    nothing, and exits successfully with no output -- indistinguishable from
    a server that started and died. Cycle 196 lost a third of an hour to it.
    """
    source = (pathlib.Path(__file__).resolve().parent.parent / "agora_runner" / "nova_site_main.py").read_text()
    assert '__name__ == "__main__"' in source
    assert source.rstrip().endswith("main()")


# --- POST /api/board/edit and /api/board/delete: The owner's issue #84 ---
# *"I need to be able to edit and especially delete boarded ideas and
# issues from the agora app."* The same two boundaries as every other
# write path here: `target` is a key into a dict of literal paths, never a
# path, and the request must be unambiguous about which of the two things
# it is asking for.

def test_editing_a_boarded_row_reaches_the_vault_through_the_real_request_path():
    with patch.object(nova_site, "edit_row", return_value=(True, "#84 edited on issues")) as ed:
        status, _, body = _post(
            "/api/board/edit", {"target": "issues", "number": 84, "title": "A better title"})
    assert status == 200
    assert json.loads(body)["ok"] is True
    ed.assert_called_once_with("issues", 84, "A better title")


def test_deleting_a_boarded_row_reaches_the_vault_through_the_real_request_path():
    with patch.object(nova_site, "remove_row", return_value=(True, "#68 deleted on ideas")) as rm:
        status, _, body = _post(
            "/api/board/delete", {"target": "ideas", "number": 68, "title": "surprise"})
    assert status == 200
    assert json.loads(body)["ok"] is True
    # Whatever a client puts in `title` on the delete route is ignored, so
    # a stray field can never turn a delete into an edit -- the same rule
    # `/api/capture/delete` follows.
    rm.assert_called_once_with("ideas", 68)


def test_a_board_edit_with_a_blank_title_is_rejected_rather_than_deleting_the_row():
    """The dangerous shape, and the reason there are two routes."""
    with patch.object(nova_site, "edit_row") as ed, patch.object(nova_site, "remove_row") as rm:
        status, _, _ = _post(
            "/api/board/edit", {"target": "issues", "number": 84, "title": "   "})
    assert status == 400
    ed.assert_not_called()
    rm.assert_not_called()


def test_a_title_cannot_smuggle_a_cell_break_or_a_line_break_into_his_table():
    for title in ["a | b", "a\nb"]:
        with patch.object(nova_site, "edit_row") as ed:
            status, _, _ = _post(
                "/api/board/edit", {"target": "issues", "number": 84, "title": title})
        assert status == 400, title
        ed.assert_not_called()


def test_board_amend_refuses_a_target_that_is_not_one_of_his_two_boards():
    """Notably a path, and notably `notes` -- a capture target that is not
    a board. Nothing a client sends addresses a vault document."""
    for target in ["notes", "projects/sokrates/projects/nova/issues", "../secrets", 7]:
        with patch.object(nova_site, "edit_row") as ed:
            status, _, _ = _post(
                "/api/board/edit", {"target": target, "number": 1, "title": "x"})
        assert status == 400, target
        ed.assert_not_called()


def test_board_amend_refuses_a_number_that_is_not_a_positive_int():
    # `True` is an int in Python and would address row 1.
    for number in [True, 0, -1, "84", 8.4, None]:
        with patch.object(nova_site, "remove_row") as rm:
            status, _, _ = _post("/api/board/delete", {"target": "issues", "number": number})
        assert status == 400, number
        rm.assert_not_called()


def test_a_row_that_moved_is_a_409_and_not_a_502():
    """Nothing failed -- the row was renumbered or removed while the page
    was open, and the page should re-read rather than retry."""
    with patch.object(nova_site, "remove_row", return_value=(False, "#99 is not a row on issues")):
        status, _, body = _post("/api/board/delete", {"target": "issues", "number": 99})
    assert status == 409
    assert json.loads(body)["ok"] is False


def test_a_failed_board_write_is_a_502():
    with patch.object(
            nova_site, "edit_row", return_value=(False, "could not write to issues: 500 boom")):
        status, _, _ = _post(
            "/api/board/edit", {"target": "issues", "number": 84, "title": "x"})
    assert status == 502


def test_a_stale_row_is_a_409_through_the_real_module_not_a_hand_typed_string():
    """The reviewer's finding: `_send_json(409 …)` keys on the substring
    `"is not a row"`, which `nova_capture._amend_board` composes. Every
    other test here mocks that function and re-types the sentence, so the
    two sides agree by inspection and nothing pins them. Rewording the
    message would turn every stale row into a 502 -- "the vault failed",
    when nothing failed -- and the page would retry instead of re-reading.

    This one runs the real `remove_row` against a board that genuinely has
    no #999, with only the vault stubbed out.
    """
    board = "---\n---\n\n## Board\n\n| # | Item | Status | Updated |\n|---|---|---|---|\n" \
            "| [[#57 — A row\\|57]] | A row | 🟡 In progress | 08-11 |\n"
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(board, "3-abc")), \
            patch.object(nova_capture, "vault_write_path") as write:
        status, _, body = _post("/api/board/delete", {"target": "issues", "number": 999})
    write.assert_not_called()
    assert status == 409, "a row that is not there was reported as a vault failure"
    assert json.loads(body)["ok"] is False


def test_a_board_edit_writes_to_his_file_and_not_to_novas_own_copy():
    """One real path end to end: a request arrives, his file is written.

    Note what this does *not* prove. It cannot tell `BOARD_PATHS` from
    `CAPTURE_TARGETS`, because the two hold the same string for `issues`
    -- swapping the lookup leaves this green. The branch where they differ
    is `notes`, and it is pinned in `test_board_row_edit.py`."""
    board = "---\n---\n\n## Board\n\n| # | Item | Status | Updated |\n|---|---|---|---|\n" \
            "| [[#57 — A row\\|57]] | A row | 🟡 In progress | 08-11 |\n"
    seen = {}
    with patch.object(nova_capture, "vault_read_path_rev",
                      side_effect=lambda p: seen.update(read=p) or (board, "3-abc")), \
            patch.object(nova_capture, "vault_write_path",
                         side_effect=lambda p, b, if_rev=None: seen.update(write=p) or "written"):
        status, _, _ = _post(
            "/api/board/edit", {"target": "issues", "number": 57, "title": "Renamed"})
    assert status == 200
    assert seen["read"] == "projects/sokrates/projects/nova/issues.md"
    assert seen["write"] == "projects/sokrates/projects/nova/issues.md"


def test_the_journal_endpoint_reports_a_payload_it_could_not_refresh():
    """The wiring, and it is the half no other test could see.

    `_with_silence` learned to tell a frozen record from a stalled loop,
    and `journal_page` learned to forward the age -- and with the endpoint
    passing `record_age=None` the whole feature is dead on the live site
    while every unit test above still passes. That mutation was run and
    caught nothing, which is the Cycle 77 lesson: a change with two halves
    has to be broken in both places separately.

    So this asks the server, over a socket, with the cache aged by hand.
    """
    nova_site.reset_cache()
    with patch.object(nova_site, "journal_payload", lambda: {"entries": [], "status": {
        "cycle": 206,
        "lastWrittenAt": "2026-08-15T04:29:00+02:00",
    }}):
        status, _, first = _get("/api/journal")
        assert status == 200
        # Built for this request, so the record is as current as it gets.
        assert json.loads(first)["status"]["recordStale"] is False

        # Age the served copy without touching anything it says: the
        # journal is byte-identical, only this process's view of it is old.
        #
        # `_refreshing` is held down over the aged read on purpose. Any age
        # past `RECORD_TRUST_SECONDS` is also past `CACHE_FRESH_SECONDS`,
        # so the next call would start a background rebuild that outlives
        # this `patch` block and run the *real* `journal_payload` against
        # the network guard. Borrowing the module's own single-flight flag
        # is what it is for, and it keeps the test to one thread.
        with nova_site._cache_lock:
            nova_site._refreshing.add("journal")
            payload, cached_body, cached_etag, built = nova_site._cache["journal"]
            nova_site._cache["journal"] = (
                payload, cached_body, cached_etag,
                built - nova_site.RECORD_TRUST_SECONDS - 1,
            )
        status, _, second = _get("/api/journal")

    nova_site.reset_cache()
    assert status == 200
    assert json.loads(second)["status"]["recordStale"] is True
    assert json.loads(second)["status"]["stalled"] is False


# --- POST /api/board/comment: idea #64, rated 🔴 and open since 08-12 ---
# *"Lets me have the same comment conversation on ideas, notes and issues
# like the Journal. Add a comment button and let me leave comments that
# discuss each idea."* The comment is appended to the row's own write-up,
# which an expanded row already fetches and renders -- so there is no read
# route here to test, and that absence is the design rather than a gap.

def test_commenting_on_a_boarded_row_reaches_the_vault_through_the_real_request_path():
    with patch.object(nova_site, "comment_on_row",
                      return_value=(True, "#64 commented on ideas")) as cm:
        status, _, body = _post(
            "/api/board/comment", {"target": "ideas", "number": 64, "text": "  Still broken. "})
    assert status == 200
    assert json.loads(body)["ok"] is True
    target, number, text, dated, author = cm.call_args[0]
    assert (target, number, text) == ("ideas", 64, "Still broken.")
    # The page is his, so an unstated author is him.
    assert author == "Edvard"
    # `MM-DD`, and Oslo's -- a module that reaches for a clock reaches for
    # it in UTC, and this lands in a file he reads.
    assert re.fullmatch(r"\d{2}-\d{2}", dated)
    assert dated == datetime.now(OSLO).strftime("%m-%d")


def test_a_cycles_reply_is_attributed_to_nova_and_not_to_him():
    """`comment_on_row` hardcoded `author="Edvard"` while its own docstring  (not-prose: quoting a literal)
    told a cycle to reply with `author="Nova"`, so every reply this loop
    made through this route was written into his board as words he had
    said. Worse than cosmetic: `unanswered_comments` calls a row waiting
    when the last note under it is his, and that flag outranks a 🔴 in
    `tools.top_board_rows` -- so a cycle commenting set a permanent "he is
    waiting" marker that its own reply could never clear."""
    with patch.object(nova_site, "comment_on_row",
                      return_value=(True, "#94 commented on issues")) as cm:
        status, _, _ = _post("/api/board/comment", {
            "target": "issues", "number": 94, "text": "Not taken this cycle.",
            "author": "Nova"})
    assert status == 200
    assert cm.call_args[0][4] == "Nova"


@pytest.mark.parametrize("author", ["Sokrates", " ", 3, "nova", "NOVA"])
def test_an_author_neither_of_us_uses_is_refused_before_any_write(author):
    """His board is not a place to write under an arbitrary name.

    `" "` is in here deliberately: it is truthy, so it survives the
    `or "Edvard"` fallback and would be written as the author verbatim.  (not-prose: quoting a literal)
    The casing pair is here because `append_detail_note` renders the
    string as given -- `**nova, 08-17:**` is not a name either of us uses.
    """
    with patch.object(nova_site, "comment_on_row") as cm:
        status, _, _ = _post("/api/board/comment", {
            "target": "issues", "number": 94, "text": "hi", "author": author})
    assert status == 400, author
    cm.assert_not_called()


@pytest.mark.parametrize("payload_extra", [{}, {"author": ""}, {"author": None}])
def test_an_unstated_author_is_him_because_the_page_is_his(payload_extra):
    with patch.object(nova_site, "comment_on_row",
                      return_value=(True, "ok")) as cm:
        status, _, _ = _post("/api/board/comment", dict(
            {"target": "issues", "number": 94, "text": "hi"}, **payload_extra))
    assert status == 200
    assert cm.call_args[0][4] == "Edvard"


def test_a_comment_cannot_smuggle_a_line_break_into_his_write_up():
    """The span failure `append_detail_note` is built around: a write-up
    ends at the next heading, so a note carrying a break truncates the
    block and every later line of his own text stops rendering."""
    for text in ["two\nlines", "sneaky\rreturn"]:
        with patch.object(nova_site, "comment_on_row") as cm:
            status, _, _ = _post(
                "/api/board/comment", {"target": "ideas", "number": 64, "text": text})
        assert status == 400, text
        cm.assert_not_called()


def test_an_empty_comment_is_rejected_rather_than_written_as_a_blank_line():
    for text in ["", "   ", None, 7]:
        with patch.object(nova_site, "comment_on_row") as cm:
            status, _, _ = _post(
                "/api/board/comment", {"target": "ideas", "number": 64, "text": text})
        assert status == 400, text
        cm.assert_not_called()


def test_a_comment_refuses_a_target_that_is_not_one_of_his_two_boards():
    """`notes` is a capture target and not a board, and it is the one a
    client is most likely to guess."""
    for target in ["notes", "projects/sokrates/projects/nova/ideas", "../secrets", 7, None]:
        with patch.object(nova_site, "comment_on_row") as cm:
            status, _, _ = _post(
                "/api/board/comment", {"target": target, "number": 64, "text": "x"})
        assert status == 400, target
        cm.assert_not_called()


def test_a_comment_refuses_a_number_that_is_not_a_positive_int():
    # `True` is an int in Python and would comment on row 1.
    for number in [True, 0, -1, "64", 6.4, None]:
        with patch.object(nova_site, "comment_on_row") as cm:
            status, _, _ = _post(
                "/api/board/comment", {"target": "ideas", "number": number, "text": "x"})
        assert status == 400, repr(number)
        cm.assert_not_called()


def test_a_row_with_no_write_up_is_a_409_and_not_a_502():
    """Nothing failed -- there is simply no block to comment under, and
    the page should say so rather than retry."""
    with patch.object(nova_site, "comment_on_row",
                      return_value=(False, "#63 is not a row on ideas")):
        status, _, body = _post(
            "/api/board/comment", {"target": "ideas", "number": 63, "text": "x"})
    assert status == 409
    assert json.loads(body)["ok"] is False


def test_an_exhausted_write_is_a_502_and_not_a_409():
    """`_amend_board` fails two ways and only one is "there is no such
    row". A losing compare-and-swap against a cycle writing the same
    write-up returns `could not write to ...`, and reporting that as 409
    makes a real failure indistinguishable from an empty row in the log.
    Caught by the reviewer; the first version sent 409 for both."""
    with patch.object(nova_site, "comment_on_row",
                      return_value=(False, "could not write to ideas: 409 conflict")):
        status, _, body = _post(
            "/api/board/comment", {"target": "ideas", "number": 64, "text": "x"})
    assert status == 502
    assert json.loads(body)["ok"] is False


def test_a_successful_comment_invalidates_the_board_he_is_looking_at():
    """The page on his phone still shows the write-up from before the
    comment; the next read must come from the file."""
    with patch.object(nova_site, "comment_on_row", return_value=(True, "ok")), \
            patch.object(nova_site, "invalidate") as inv:
        _post("/api/board/comment", {"target": "ideas", "number": 64, "text": "x"})
    inv.assert_called_once_with("board:ideas")


# --- Clearing a Needs Edvard item from the page (issue #93) ---------------  (not-prose: quoting a literal)




def test_the_plan_page_and_its_endpoint_both_answer():
    """The same pair again, for the same reason (`issues.md` #7).

    `nova_plan`'s own tests call the shaping directly and the browser
    tests stub `fetch`, so nothing else here would notice `/plan` or
    `/api/plan` disappearing and the nav tab 404ing on his phone. That
    is not hypothetical on this page: the two documents it serves live
    in the owner's own database, which this process reaches with a
    different credential from the one the boards use.

    The second half asserts what a *missing* document does, because it
    is the difference between a page that says "not written yet" and a
    nav tab that 502s. `plan_markdown` reads two paths through one
    patched `vault_read_path`, so returning `None` stands in for both
    files being absent -- the state a fresh vault is in.
    """
    roadmap = "---\nupdated: 2026-08-16\n---\n\n# Roadmap\n\n## The five I would do next\n\nCI first.\n"
    with patch.object(nova_sources, "vault_read_path", return_value=roadmap):
        nova_site.reset_cache()
        status, _, body = _get("/api/plan")
        shell_status, _, shell = _get("/plan")
    assert status == 200
    payload = json.loads(body)
    assert [doc["key"] for doc in payload["documents"]] == ["roadmap", "goals"]
    assert payload["documents"][0]["title"] == "Roadmap"
    assert any(
        section["heading"] == "The five I would do next"
        for section in payload["documents"][0]["sections"]
    )
    assert shell_status == 200 and b"<!doctype html>" in shell.lower()

    with patch.object(nova_sources, "vault_read_path", return_value=None):
        nova_site.reset_cache()
        empty_status, _, empty = _get("/api/plan")
    assert empty_status == 200
    assert all(doc["missing"] for doc in json.loads(empty)["documents"])


def test_the_notes_page_and_its_endpoint_both_answer():
    """The same pair as `/plan`, for the same reason (`issues.md` #7).

    `nova_notes`' own tests call the shaping directly and the browser
    tests stub `fetch`, so nothing else here would notice `/notes` or
    `/api/notes` disappearing and the nav tab 404ing on his phone.

    The owner's capture, 2026-08-21: *"I do not have a notes page that shows
    any overview of the notes made."* `notes.md` is in his own database,
    which this process reaches with a different credential from the one
    the boards use -- so the empty half matters here as much as it does
    on `/plan`: a vault where he has never left a note must render an
    empty page, not a 502.
    """
    markdown = (
        "---\ntype: log\n---\n\n- Waiting on someone.\n- \n\n"
        "## Read\n\n- Answered one.\n  - Read Cycle 258. Did the thing.\n"
    )
    with patch.object(nova_sources, "vault_read_path", return_value=markdown):
        nova_site.reset_cache()
        status, _, body = _get("/api/notes")
        shell_status, _, shell = _get("/notes")
    assert status == 200
    payload = json.loads(body)
    assert payload["waitingTotal"] == 1 and payload["readTotal"] == 1
    # Oldest first, unanswered last -- the page is a conversation and it
    # opens scrolled to the bottom (`nova_notes.notes_payload`).
    assert payload["notes"][0]["responses"][0]["cycle"] == 258
    assert payload["notes"][1]["text"] == "Waiting on someone."
    assert shell_status == 200 and b"<!doctype html>" in shell.lower()

    with patch.object(nova_sources, "vault_read_path", return_value=None):
        nova_site.reset_cache()
        empty_status, _, empty = _get("/api/notes")
    assert empty_status == 200
    assert json.loads(empty)["notes"] == []


def test_capturing_a_note_clears_the_notes_page_cache():
    """The failure the old comment beside `invalidate` predicted.

    It said `board:notes` never exists because notes had no page. Now
    they have one, cached under its own name -- so without the second
    invalidate, tapping Note and landing on `/notes` shows the file as
    it was *before* the note the app just told him it saved.
    """
    with patch.object(nova_site, "capture", return_value=(True, "ok")), \
            patch.object(nova_site, "invalidate") as inv:
        _post("/api/capture", {"target": "notes", "text": "a new note"})
    assert [call.args[0] for call in inv.call_args_list] == ["board:notes", "notes"]


def test_service_worker_precaches_the_chart_library():
    """The costs page has to draw with the tailnet down, and it was the one
    page that could not.

    `app.js` loads ECharts lazily on the first chart, so the worker's
    network-first fetch handler only ever files it away *after* a visit
    made while online. A phone that installs the app and then loses the
    link therefore has every page but this one, which is the exact failure
    vendoring the library instead of using a CDN was meant to prevent.

    Both halves are asserted together on purpose: the path the worker
    precaches has to be a path this server actually answers, and nothing
    else checks that those two agree. `cache.addAll` is atomic, so a
    precache entry the server 404s does not degrade -- it fails the whole
    install and the app stops being offline-capable at all.
    """
    status, _, worker = _get("/sw.js")
    assert status == 200
    assert b'"/vendor/echarts.min.js"' in worker

    served, _, library = _get("/vendor/echarts.min.js")
    assert served == 200
    assert len(library) > 500_000
