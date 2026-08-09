"""Nova's read-only site: the parsers, and the real request path.

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

import importlib
import io
import json
import os
from unittest.mock import patch

import pytest

from agora_runner import nova_site
from agora_runner.nova_journal import (
    build_status,
    parse_digest,
    parse_heading,
    parse_journal,
    render_blocks,
    render_inline,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def journal_md():
    return _fixture("journal_sample.md")


@pytest.fixture(scope="module")
def digest_md():
    return _fixture("digest_sample.md")


@pytest.fixture(scope="module")
def entries(journal_md):
    return parse_journal(journal_md)


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
    entry = parse_journal("## Entries\n\n### 2026-08-09 01:00 (Oslo) — Cycle 1\n\n" + body)[0]
    assert entry["pr"] == "#12"
    assert entry["outcome"] == "shipped"
    assert "A section after a horizontal rule" in entry["body"]


def test_an_entry_with_no_footer_still_parses():
    entry = parse_journal("## Entries\n\n### 2026-08-09 01:00 (Oslo) — Cycle 1\n\nJust prose.")[0]
    assert entry["pr"] == ""
    assert entry["outcome"] == ""
    assert entry["body"] == "Just prose."


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


@pytest.mark.parametrize("text", ["Nothing.", "Nothing", "none", "  \n"])
def test_needs_edvard_is_empty_when_it_says_nothing(text):
    assert parse_digest(f"## Needs Edvard\n\n{text}\n\n## Digest\n")["hasNeedsEdvard"] is False


def test_needs_edvard_is_present_when_it_asks_something():
    digest = parse_digest("## Needs Edvard\n\nShould the site be public?\n\n## Digest\n")
    assert digest["hasNeedsEdvard"] is True
    assert "public" in digest["needsEdvard"]


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


def _get(path):
    sock = _FakeSocket(f"GET {path} HTTP/1.1\r\nHost: nova\r\n\r\n".encode())
    nova_site.NovaSiteHandler(sock, ("127.0.0.1", 50000), _FakeServer())
    raw = sock.sent.getvalue()
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, head.decode("latin-1"), body


def test_root_serves_the_shell():
    status, head, body = _get("/")
    assert status == 200
    assert "text/html" in head
    assert b'<div id="feed"' in body or b'id="feed"' in body


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
    with patch.object(nova_site, "vault_read_path", return_value=journal_md):
        status, head, body = _get("/api/journal")
    assert status == 200
    assert "application/json" in head
    payload = json.loads(body)
    assert payload["status"]["cycle"] == 49
    assert len(payload["entries"]) == 5
    assert payload["entries"][0]["blocks"][0]["type"] == "p"
    assert "body" not in payload["entries"][0]


def test_api_digest_returns_the_needs_section_rendered(digest_md):
    with patch.object(nova_site, "vault_read_path", return_value=digest_md):
        status, _, body = _get("/api/digest")
    payload = json.loads(body)
    assert status == 200
    assert "needsEdvardBlocks" in payload
    assert isinstance(payload["hasNeedsEdvard"], bool)
    assert payload["lines"]


def test_a_missing_vault_file_is_an_empty_journal_not_a_500():
    with patch.object(nova_site, "vault_read_path", return_value=None):
        status, _, body = _get("/api/journal")
    assert status == 200
    assert json.loads(body)["entries"] == []


def test_a_vault_failure_is_reported_as_502():
    with patch.object(nova_site, "vault_read_path", side_effect=RuntimeError("couchdb down")):
        status, _, body = _get("/api/journal")
    assert status == 502
    assert "couchdb down" in json.loads(body)["error"]


def test_the_site_is_read_only():
    """No write verb is implemented at all -- not refused at runtime,
    simply absent, so adding one has to be a deliberate act."""
    for verb in ("do_POST", "do_PUT", "do_PATCH", "do_DELETE"):
        assert not hasattr(nova_site.NovaSiteHandler, verb)


def test_start_nova_site_binds_and_serves_the_real_handler():
    """Drives the actual entry point main() calls. Eight QuotaWatcher tests
    in the bridge all called `_run()` directly and left `start()` covered by
    nothing; this is the same seam, so it gets the real function."""
    with patch.object(nova_site, "NOVA_PORT", 0):
        server = nova_site.start_nova_site()
    try:
        assert server.RequestHandlerClass is nova_site.NovaSiteHandler
        assert server.server_address[1] != 0
    finally:
        server.shutdown()
        server.server_close()


def test_main_actually_starts_the_site():
    """Runs the real `main()`. Asserting the import binding instead would
    pass just as well with the call deleted from main, which is exactly
    the class of gap this suite exists to stop shipping.

    `_Stop` derives from BaseException on purpose: main's poll loop
    catches `Exception` and keeps going, so anything narrower would spin
    forever instead of ending the test. `agora_runner.main` the module is
    shadowed by `main` the function on the package, hence the explicit
    import.
    """

    class _Stop(BaseException):
        pass

    main_module = importlib.import_module("agora_runner.main")
    started = []
    with patch.object(main_module, "start_invoke_server"), patch.object(
        main_module, "start_nova_site", side_effect=lambda: started.append(True)
    ), patch.object(main_module, "poll_once", side_effect=_Stop):
        with pytest.raises(_Stop):
            main_module.main()
    assert started == [True]
