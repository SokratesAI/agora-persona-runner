"""The Heartbeats page has to refresh itself.

Everything on that page changes without the owner touching it -- a run starting,
a run finishing, `lastResult` going from nothing to a real answer -- and it
drew once and then sat there. Press "Run now" and the row reads
"On · run queued" until the app is reloaded.

No test in this repo executes `app.js`; the convention is to assert on its
source text, and for a poller that is close to vacuous -- "the file contains
the word setTimeout" passes whether or not anything is ever scheduled. So
these tests cut the four functions that decide the behaviour out of the
shipped file and run them under `node` against stubs, which means they fail
if the cadence is wrong, if the queued-run case stops being the fast one, or
if a failed fetch stops rescheduling. The source is the real source: the
extraction is by brace matching from the actual file, not a copy.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "agora_runner" / "nova_public" / "app.js"


def extract_function(source: str, name: str) -> str:
    """The text of `function <name>(...) { ... }`, by brace matching.

    Deliberately not a regex: the bodies here contain braces, strings and
    comments, and a regex that looked like it worked would quietly return a
    prefix. A brace counter over the real characters is wrong only on a brace
    inside a string literal in these two functions, and there is none -- if
    one ever appears the extraction fails loudly by producing unparseable
    JavaScript, which `node` then refuses.
    """
    start = source.index("function " + name + "(")
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError("unbalanced braces reading " + name)


def run_poll_harness(rows, fail_fetch=False, view="heartbeats"):
    """Run `scheduleHeartbeatsPoll` (and, on failure, `loadHeartbeats`) once.

    The success cases go in through the real `renderHeartbeats`, not through
    `scheduleHeartbeatsPoll` directly: a first version of this file called the
    scheduler itself, and a mutation round showed that deleting the one line
    that wires it into the render left every test passing with the bug fully
    intact. The wiring is the fix; the cadence is only the detail.

    Returns the list of delays handed to `setTimeout`, in milliseconds.
    """
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI has node; a dev box may not
        pytest.skip("node is not installed")

    source = APP_JS.read_text(encoding="utf-8")
    harness = textwrap.dedent(
        """
        var ASK_POLL_MS = 4000;
        var POLL_MS = 30000;
        var delays = [];
        var livePolls = [];
        var view = VIEW;
        var painted = null;
        function setTimeout(fn, ms) { delays.push(ms); return delays.length; }
        function route() { return { view: view }; }
        var window = { location: { pathname: "/heartbeats" } };
        function markNav() {}
        function stopPolling() { livePolls = []; }
        function fmtStamp(ms) { return "at " + ms; }
        function node(tag, cls, text) {
          return {
            tag: tag, cls: cls, text: text, children: [],
            appendChild: function (n) { this.children.push(n); painted = n; return n; },
            setAttribute: function () {}, addEventListener: function () {},
          };
        }
        function el(tag, cls, text) { return node(tag, cls, text); }
        var statusEl = node("div", "", "");
        var feed = node("div", "", "");
        function fetchPage() { return FETCH; }
        __FUNCTIONS__
        __CALL__
        console.log(JSON.stringify({ delays: delays, painted: painted }));
        """
    )
    functions = "\n".join(
        extract_function(source, name)
        for name in ("hbStateLine", "scheduleHeartbeatsPoll", "renderHeartbeats", "loadHeartbeats")
    )
    if fail_fetch:
        call = 'var FETCH = Promise.reject("boom"); loadHeartbeats();'
        # The rejection is handled a microtask later, so print after it settles.
        harness = harness.replace(
            "console.log(", "Promise.resolve().then(function () {}).then(function () {\n  console.log("
        ).rstrip() + "\n});"
    else:
        call = ("var FETCH = Promise.resolve({});\n"
                "renderHeartbeats({ heartbeats: " + json.dumps(rows) + " });")

    script = ("var VIEW = " + json.dumps(view) + ";\n"
              + harness.replace("__FUNCTIONS__", functions).replace("__CALL__", call))
    out = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_queued_run_is_polled_at_the_fast_cadence():
    """`forceRun` is exactly the state his complaint describes.

    He presses "Run now", the write answers `{ok: true}` with no row in it,
    the page re-lists and draws "On · run queued". The next thing that
    changes is Agora picking the run up, and 30 seconds is long enough that
    it reads as broken.
    """
    assert run_poll_harness([{"enabled": True, "forceRun": True}])["delays"] == [4000]


def test_a_running_heartbeat_is_polled_at_the_fast_cadence():
    assert run_poll_harness([{"enabled": True, "running": True}])["delays"] == [4000]


def test_an_idle_page_falls_back_to_the_journal_feed_rate():
    """Nothing in flight is the common case and it must not poll every 4s.

    A cycle runs every 20 minutes; a page left open on the idle rate asks
    twice a minute, which is what the journal feed already does.
    """
    rows = [{"enabled": True}, {"enabled": False}, {"enabled": True, "running": False}]
    assert run_poll_harness(rows)["delays"] == [30000]


def test_an_empty_page_still_polls():
    """A first heartbeat created from Agora's side should appear unaided."""
    assert run_poll_harness([])["delays"] == [30000]


def test_a_failed_fetch_reschedules_instead_of_freezing_the_page():
    """One dropped request on a phone must not stop the page forever.

    This is the same bug as the one being fixed, wearing a different hat:
    before, the page stopped polling because it never started; here it would
    stop because it gave up.
    """
    result = run_poll_harness([], fail_fetch=True)
    assert result["delays"] == [30000]
    assert "Could not load your heartbeats" in (result["painted"] or {}).get("text", "")


def test_a_failure_after_he_navigates_away_paints_nothing():
    """The timer can have a request in flight when he taps to another page.

    Without the route guard on the failure path, that request failing writes
    "Could not load your heartbeats" over whatever page he actually opened --
    an exposure this cycle created by making the fetch repeat.
    """
    result = run_poll_harness([], fail_fetch=True, view="journal")
    assert result["painted"] is None
    assert result["delays"] == []
