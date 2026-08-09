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

import importlib
import io
import json
import os
import re
from unittest.mock import patch

import pytest

from agora_runner import nova_capture, nova_site
from agora_runner.nova_journal import (
    build_status,
    parse_digest,
    parse_heading,
    parse_journal,
    render_blocks,
    render_inline,
    split_outcome,
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
    assert [e["cycle"] for e in parse_journal(markdown)] == [1]


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
    cap.assert_called_once_with("issues", "the app needs a restart")


@pytest.mark.parametrize("target", ["issues", "ideas"])
def test_both_targets_are_accepted(target):
    with patch.object(nova_site, "capture", return_value=(True, "ok")) as cap:
        status, _, _ = _post("/api/capture", {"target": target, "text": "x"})
    assert status == 200
    assert cap.call_args[0][0] == target


@pytest.mark.parametrize("payload", [
    {"target": "journal", "text": "x"},
    {"target": "../../etc/passwd", "text": "x"},
    {"target": "projects/sokrates/projects/agora/issues.md", "text": "x"},
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
    """The cap is a memory bound, not an opinion about how much Edvard may
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
