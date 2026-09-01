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
# Read off the shipped file so the bound under test is the real one.
ASK_POLL_MAX_VALUE = int(
    [ln.strip() for ln in APP_JS.read_text(encoding="utf-8").splitlines()
     if ln.strip().startswith("var ASK_POLL_MAX = ")][0].split("=")[1].strip(" ;")
)


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


def extract_var(source: str, name: str) -> str:
    """The single-line `var <name> = <literal>;` declaration, verbatim."""
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("var " + name + " = ") and stripped.endswith(";"):
            return stripped
    raise AssertionError("app.js no longer declares " + name + " on one line")


def run_poll_harness(rows, fail_fetch=False, view="heartbeats", seeded=(), ticks=1):
    """`seeded` puts timers in `livePolls` before the code under test runs.

    Without it the failure path cannot be tested at all: `livePolls` starts
    empty, so a missing `stopPolling()` and a present one produce byte-identical
    output. That was a real hole -- my reviewer found the missing
    `stopPolling()` in the catch branch and proved this file could not see it.

    Run `scheduleHeartbeatsPoll` (and, on failure, `loadHeartbeats`) once.

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
__CONSTANTS__
        var delays = [];
        var livePolls = SEEDED.slice();
        var cleared = 0;
        var view = VIEW;
        var painted = null;
        function setTimeout(fn, ms) { delays.push(ms); return delays.length; }
        function route() { return { view: view }; }
        var window = { location: { pathname: "/heartbeats" } };
        function markNav() {}
        function stopPolling() { cleared += livePolls.length; livePolls = []; }
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
        console.log(JSON.stringify({ delays: delays, painted: painted, live: livePolls.length, cleared: cleared }));
        """
    )
    # Read the cadence constants out of the shipped file rather than hardcoding
    # them. My reviewer's second finding: a harness that declares its own
    # `var ASK_POLL_MS = 4000` passes for ever after someone changes the real
    # one, and the docstring above claiming "the source is the real source"
    # would be false of exactly the numbers under test.
    constants = "\n".join(
        extract_var(source, name) for name in ("ASK_POLL_MS", "ASK_POLL_MAX", "POLL_MS")
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
                "for (var t = 0; t < " + str(ticks) + "; t++) {\n"
                "  renderHeartbeats({ heartbeats: " + json.dumps(rows) + " });\n"
                "}")

    harness = harness.replace("__CONSTANTS__", constants)
    script = ("var VIEW = " + json.dumps(view) + ";\n"
              + "var SEEDED = " + json.dumps(seeded) + ";\n"
              + "var hbFastTicks = 0;\n"
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


def test_a_queued_run_wins_over_idle_rows():
    """One queued row among many idle ones still gets the fast rate."""
    rows = [{"running": True}, {"enabled": False}, {"enabled": True, "forceRun": True}]
    assert run_poll_harness(rows)["delays"] == [4000]


def test_a_run_already_under_way_uses_the_idle_cadence():
    """`running` deliberately does not trigger the fast rate.

    It is `lastResult === "running"`, and the Nova row is running for about
    eighteen of every twenty minutes -- so keying the fast cadence on it makes
    the fast cadence permanent, ~900 requests an hour per open tab, for a row
    that changes twice. Measured against the live endpoint while writing this:
    of the seven heartbeats, the one running was Nova's own.
    """
    assert run_poll_harness([{"enabled": True, "running": True}])["delays"] == [30000]


def test_an_idle_page_falls_back_to_the_journal_feed_rate():
    """Nothing in flight is the common case and it must not poll every 4s.

    A cycle runs every 20 minutes; a page left open on the idle rate asks
    twice a minute, which is what the journal feed already does.
    """
    rows = [{"enabled": True}, {"enabled": False}, {"enabled": True, "forceRun": False}]
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


def test_a_failure_clears_the_pending_timer_before_scheduling_its_own():
    """Two timers alive at once was reachable with two ordinary taps.

    `loadHeartbeats` is called from `hbPost` as well as from the poll chain,
    and `button.disabled` guards only the button that was tapped -- so "Turn
    off" on one row and "Run now" on another put two requests in flight. If
    the success settles first it schedules a timer; a failure settling after
    it appends a second without clearing the first, and only a success ever
    clears. My reviewer found this and proved the first version of this file
    could not see it, because `livePolls` always started empty here.
    """
    result = run_poll_harness([], fail_fetch=True, seeded=[99])
    assert result["cleared"] == 1, "the pending timer was not cleared"
    assert result["live"] == 1, "exactly one timer should be alive afterwards"


def test_the_fast_phase_stops_after_four_minutes():
    """`forceRun` is not the few-seconds flag I first took it for.

    `nova_heartbeats.run_now`'s own docstring: pressing it during a cycle
    means the run happens when that cycle ends, "which can be most of an hour
    later". `/api/heartbeats` is uncached and makes two upstream Agora calls
    per request, so an uncapped fast phase is ~1,800 upstream calls an hour
    from one open tab. The polling never stops; the 4-second phase does.
    """
    rows = [{"enabled": True, "forceRun": True}]
    delays = run_poll_harness(rows, ticks=ASK_POLL_MAX_VALUE + 3)["delays"]
    assert delays[:3] == [4000, 4000, 4000], "the first ticks should be fast"
    assert delays[ASK_POLL_MAX_VALUE - 1] == 4000, "the last fast tick"
    assert delays[ASK_POLL_MAX_VALUE] == 30000, "then it drops to the idle rate"
    assert set(delays[ASK_POLL_MAX_VALUE:]) == {30000}, "and stays there"
