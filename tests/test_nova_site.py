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
import importlib
import inspect
import io
import json
import os
import re
import signal
import sys
from unittest.mock import patch

import pytest

from agora_runner import nova_capture, nova_site
from agora_runner.nova_journal import (
    assign_emoji,
    build_status,
    is_empty_needs,
    parse_digest,
    parse_heading,
    parse_journal,
    parse_pr_refs,
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
        with patch.object(nova_site, "NOVA_PORT", 0):
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
        with patch.object(nova_site, "NOVA_PORT", 0):
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


# --- Cycle 56: the emoji Edvard asked for, and the collapsed card ---


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
    with patch.object(nova_site, "vault_read_path", return_value=_fixture("journal_sample.md")):
        payload = nova_site.journal_payload()
    assert payload["entries"]
    assert all(entry.get("emoji") for entry in payload["entries"])


def test_a_collapsed_card_hides_the_body_without_dropping_it():
    """Edvard asked for cards that collapse to 2-3 lines. The summary is
    clamped in CSS and the body hidden by a class -- both still in the DOM.
    A server-side truncation would have been the easy version and would
    have put the prose out of reach of find-in-page for good."""
    css = open(
        os.path.join(os.path.dirname(nova_site.PUBLIC_DIR), "nova_public", "style.css"),
        encoding="utf-8",
    ).read()
    assert ".entry.is-collapsed .entry-body" in css
    assert "-webkit-line-clamp: 3" in css

    source = open(
        os.path.join(os.path.dirname(nova_site.PUBLIC_DIR), "nova_public", "app.js"),
        encoding="utf-8",
    ).read()
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    assert "substring(" not in code and "slice(0," not in code


# --- The `Needs Edvard` box, and the emphasis that kept it on screen -------


@pytest.mark.parametrize(
    "text",
    ["**Nothing.**", "*Nothing*", "__none__", "`nothing`", "**None**", "Nothing.", "  ", ""],
)
def test_a_bolded_nothing_is_still_nothing(text):
    """Edvard, issues.md 2026-08-09: "The 'needs Edvard' box should not show
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


def test_the_live_digests_needs_section_is_hidden():
    """The bug as Edvard actually met it, against the committed fixture of
    the file he reads."""
    assert parse_digest(_fixture("digest_two_entries.md"))["hasNeedsEdvard"] is False


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
    that makes "leave it as is" (Edvard's words) checkable rather than
    asserted -- linkifying is allowed to add structure and never to edit."""
    fields = {e["pr"] for e in parse_journal(journal_md) if e["pr"]}
    assert fields
    for field in fields:
        assert "".join(s["text"] for s in parse_pr_refs(field)) == field


def test_the_payload_carries_the_pr_spans_the_client_reads():
    with patch.object(nova_site, "vault_read_path", return_value=_fixture("journal_sample.md")):
        payload = nova_site.journal_payload()
    assert payload["entries"]
    assert all("prSpans" in entry for entry in payload["entries"])


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
    """Edvard, issues.md 2026-08-09: "the Digest is hard to read with grey
    against the blue background. White is better like the actual journal."

    Worth a test rather than a look, because this is the one change of the
    four that a green suite said nothing about: reverting it left all 1049
    other tests passing. A styling fix nothing pins is a styling fix the
    next refactor silently undoes."""
    declarations = _css_rule(".entry-summary")
    assert "color: var(--text)" in declarations
    assert "var(--dim)" not in declarations


def test_a_digest_line_carries_its_bold_as_spans_not_asterisks():
    """Nearly every digest line opens with a bolded sentence. The card
    rendered `text` verbatim, so it was the only text on the page showing
    its own markup -- and it is the same line Edvard called hard to read.
    Found by rendering the live files rather than the fixtures, which do
    not happen to contain a bold digest line."""
    lines = parse_digest(_fixture("digest_two_entries.md"))["lines"]
    assert lines
    bolded = [line for line in lines if "**" in line["text"]]
    assert bolded, "the fixture has no bold digest line to check"
    for line in bolded:
        kinds = {span["kind"] for span in line["spans"]}
        assert "strong" in kinds
        assert all("**" not in span["text"] for span in line["spans"])
        assert "".join(s["text"] for s in line["spans"]) == line["text"].replace("**", "")
