"""Tests for `tools.nas`.

Nothing here can reach a real Sonarr or Radarr -- the NAS is not on
the tailnet yet -- so every test drives the module through its injected
`get`, and the two tests that must not be faked away drive `_get` itself
against a stub opener. Those are the ones that matter: the read-only
boundary and the header, because a passing test that never built a real
request would prove nothing about either.
"""

import datetime as dt
import io
import json
import urllib.error
import urllib.request
import pathlib
import subprocess
import types

import pytest

from tools import nas

SSH_STUB = {"host": "nas.example", "user": "nova", "key": "/etc/nas-ssh/id_ed25519"}


# --- the read-only boundary -------------------------------------------------


def test_get_refuses_a_path_outside_the_allowlist():
    # With a positive control, because a stub opener that is never called
    # proves nothing unless it would have been called for a legal path.
    calls = []
    conf = {"url": "http://nas:8989", "key": "k"}

    nas._get("sonarr", conf, "/api/v3/series", opener=_stub_opener([], calls))
    assert len(calls) == 1

    with pytest.raises(ValueError):
        nas._get("sonarr", conf, "/api/v3/command", opener=_stub_opener([], calls))
    assert len(calls) == 1  # the forbidden path never reached the network


def test_the_allowlist_grants_nothing_the_module_does_not_call():
    # The old test here asserted four string literals were absent from a set
    # literal in the same repo. It exercised nothing and would have passed
    # with no allowlist check at all. This one reads the source and fails if
    # the two ever drift -- which they had: two movie paths were granted and
    # never called.
    #
    # It reads the whole `tools/` package rather than `nas.py` alone, because
    # a grant's caller may be a sibling module -- `/api/v3/notification` is
    # allowed here and called by `tools.nas_watch`. The invariant is that a
    # granted path has a caller somewhere, not that `nas.py` is the caller.
    source = pathlib.Path(nas.__file__).read_text()
    body = source.split("READ_ONLY = {", 1)[1].split("}", 1)[1]
    siblings = "".join(
        f.read_text() for f in sorted(pathlib.Path(nas.__file__).parent.glob("*.py"))
        if f.name != "nas.py"
    )
    for path in nas.READ_ONLY:
        assert f'"{path}"' in body + siblings, f"{path} is allowed but never called"


def test_a_redirect_is_refused_rather_than_followed():
    # urllib copies headers onto the redirect target with no same-origin
    # check, so following one would hand the API key to whoever answered.
    handler = nas._NoRedirect()
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(
            urllib.request.Request("http://nas:8989/api/v3/calendar"),
            io.BytesIO(b""), 302, "Found", {}, "http://elsewhere.example/",
        )


def _stub_opener(payload, captured):
    class Resp:
        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False

        def read(self_inner):
            return json.dumps(payload).encode()

    def opener(req, timeout=None):
        captured.append(req)
        return Resp()

    return opener


def test_get_sends_the_api_key_header_and_a_hardcoded_get():
    captured = []
    out = nas._get(
        "sonarr",
        {"url": "http://nas:8989", "key": "secret"},
        "/api/v3/calendar",
        {"start": "2026-08-27", "end": None},
        opener=_stub_opener([{"title": "x"}], captured),
    )
    req = captured[0]
    assert req.get_method() == "GET"
    assert req.headers["X-api-key"] == "secret"
    assert req.data is None
    # `end=None` is dropped rather than sent as the string "None"
    assert req.full_url == "http://nas:8989/api/v3/calendar?start=2026-08-27"
    assert out == [{"title": "x"}]


def test_a_200_that_is_not_json_is_unreachable_not_success():
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html>captive portal</html>"

    with pytest.raises(nas.Unreachable):
        nas._get("sonarr", {"url": "http://nas:8989", "key": "k"}, "/api/v3/system/status", opener=lambda r, timeout=None: Resp())


def test_a_401_says_the_key_was_refused():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))

    with pytest.raises(nas.Unreachable) as exc:
        nas._get("sonarr", {"url": "http://nas:8989", "key": "k"}, "/api/v3/system/status", opener=opener)
    assert "refused the API key" in str(exc.value)


# --- configuration ----------------------------------------------------------
#
# Every `config` call below passes `ssh=None`. Without it the result depends on
# which pod ran the suite -- the runner pod has ssh and the sealed key, so
# `config({})` there opens a real connection to the NAS and comes back with two
# services configured, and these same assertions would fail on that pod alone.


def test_config_needs_both_a_url_and_a_key():
    assert nas.config({"SONARR_URL": "http://nas:8989"}, ssh=None) == {}
    assert nas.config({"SONARR_API_KEY": "k"}, ssh=None) == {}
    assert nas.config({"SONARR_URL": "http://nas:8989", "SONARR_API_KEY": "k"}, ssh=None) == {
        "sonarr": {"url": "http://nas:8989", "key": "k"}
    }


def test_config_accepts_a_bare_host_and_trims_a_trailing_slash():
    assert nas.config({"RADARR_URL": "nas:7878/", "RADARR_API_KEY": "k"}, ssh=None)["radarr"]["url"] == "http://nas:7878"


def test_one_configured_service_is_enough():
    conf = nas.config({"RADARR_URL": "http://nas:7878", "RADARR_API_KEY": "k"}, ssh=None)
    assert set(conf) == {"radarr"}


def test_main_with_nothing_configured_names_the_four_values_and_exits_1():
    out = io.StringIO()
    # `ssh=None` is "there is no hop", stated rather than inherited from
    # whichever pod runs the suite -- the runner pod has ssh and a key, the
    # bridge pod has neither, and a test that reads either of those is a test
    # whose result depends on where it ran.
    assert nas.main(["status"], env={}, out=out, ssh=None) == 1
    text = out.getvalue()
    for value in ("SONARR_URL", "SONARR_API_KEY", "RADARR_URL", "RADARR_API_KEY"):
        assert value in text
    # It names which half is missing rather than only what to set: on this pod
    # the answer is usually the ssh binary, not a value he forgot.
    assert "ssh" in text
    assert "port 22" in text


# --- time -------------------------------------------------------------------


def test_air_times_are_converted_from_utc_to_oslo():
    # 19:00Z in August is 21:00 in Oslo. Reading the Z as local is the
    # exact mistake that invented a 100-minute delay in Cycle 446's report.
    assert nas._to_oslo("2026-08-27T19:00:00Z") == "27 Aug 21:00"


def test_air_times_in_january_use_the_winter_offset():
    assert nas._to_oslo("2026-01-15T19:00:00Z") == "15 Jan 20:00"


def test_an_unparseable_stamp_comes_back_untouched_rather_than_invented():
    assert nas._to_oslo("soon") == "soon"
    assert nas._to_oslo(None) is None


# --- calendar ---------------------------------------------------------------


CONF = {"sonarr": {"url": "http://s", "key": "k"}, "radarr": {"url": "http://r", "key": "k"}}


def test_calendar_asks_for_the_window_it_was_given():
    seen = {}

    def get(service, conf, path, params=None):
        seen[service] = params
        return []

    nas.calendar(CONF, days=3, get=get, today=dt.date(2026, 8, 27))
    # includeSeries defaults to false, and without it every episode line reads
    # "unknown series" against a real Sonarr.
    assert seen["sonarr"] == {"start": "2026-08-27", "end": "2026-08-30", "includeSeries": "true"}
    assert seen["radarr"] == {"start": "2026-08-27", "end": "2026-08-30"}


def test_calendar_renders_an_episode_and_a_movie():
    def get(service, conf, path, params=None):
        if service == "sonarr":
            return [
                {
                    "series": {"title": "The Great British Bake Off"},
                    "seasonNumber": 17,
                    "episodeNumber": 2,
                    "title": "Cake Week",
                    "airDateUtc": "2026-08-28T19:00:00Z",
                    "hasFile": False,
                }
            ]
        return [{"title": "Dune: Part Three", "year": 2026, "digitalRelease": "2026-08-29T00:00:00Z", "hasFile": True}]

    lines, failures = nas.calendar(CONF, days=7, get=get)
    assert failures == []
    assert "28 Aug 21:00  The Great British Bake Off S17E02 — Cake Week (not downloaded)" in lines
    assert "2026-08-29  Dune: Part Three (2026) (have it)" in lines


def test_one_dead_service_does_not_take_the_other_with_it():
    def get(service, conf, path, params=None):
        if service == "radarr":
            raise nas.Unreachable("radarr did not answer: timed out")
        return [{"series": {"title": "S"}, "seasonNumber": 1, "episodeNumber": 1, "title": "E", "airDateUtc": "2026-08-28T19:00:00Z"}]

    lines, failures = nas.calendar(CONF, days=7, get=get)
    assert len(lines) == 1
    assert failures == ["radarr did not answer: timed out"]


def test_main_calendar_exits_1_when_a_service_failed_but_still_prints_the_rest():
    def get(service, conf, path, params=None):
        if service == "radarr":
            raise nas.Unreachable("radarr refused the API key (401)")
        return [{"series": {"title": "S"}, "seasonNumber": 1, "episodeNumber": 1, "title": "E", "airDateUtc": "2026-08-28T19:00:00Z"}]

    out = io.StringIO()
    env = {"SONARR_URL": "http://s", "SONARR_API_KEY": "k", "RADARR_URL": "http://r", "RADARR_API_KEY": "k"}
    assert nas.main(["calendar", "--days", "2"], env=env, get=get, out=out) == 1
    assert "S S01E01" in out.getvalue()
    assert "could not read: radarr refused the API key (401)" in out.getvalue()


def test_main_calendar_says_so_when_there_is_genuinely_nothing():
    out = io.StringIO()
    env = {"SONARR_URL": "http://s", "SONARR_API_KEY": "k"}
    assert nas.main(["calendar"], env=env, get=lambda *a, **k: [], out=out) == 0
    assert "nothing airing or releasing in the next 7 day(s)" in out.getvalue()


# --- airing -----------------------------------------------------------------


LIBRARY = [
    {"title": "The Great British Bake Off", "nextAiring": "2026-08-28T19:00:00Z", "ended": False},
    {"title": "The Wire", "ended": True, "previousAiring": "2008-03-09T01:00:00Z"},
    {"title": "Some Hiatus Show", "ended": False, "previousAiring": "2025-06-01T19:00:00Z"},
]


def _library_get(found=()):
    def get(service, conf, path, params=None):
        if path == "/api/v3/series":
            return LIBRARY
        if path == "/api/v3/series/lookup":
            return list(found)
        raise AssertionError(path)

    return get


def test_airing_matches_his_spelling_against_the_real_title():
    # He types "great British bakeoff"; Sonarr holds "The Great British Bake Off".
    lines = nas.airing(CONF, "great British bakeoff", get=_library_get())
    assert lines == ["The Great British Bake Off: next episode 28 Aug 21:00 (Oslo)"]


def test_airing_says_ended_rather_than_pretending_a_date_exists():
    lines = nas.airing(CONF, "the wire", get=_library_get())
    assert lines == ["The Wire: ended, last aired 09 Mar 02:00"]


def test_a_show_on_hiatus_is_not_reported_as_ended():
    lines = nas.airing(CONF, "hiatus", get=_library_get())
    assert lines == ["Some Hiatus Show: in your library, no next air date known (last aired 01 Jun 21:00)"]


def test_a_show_he_does_not_own_is_labelled_as_not_his():
    lines = nas.airing(CONF, "severance", get=_library_get([{"title": "Severance", "year": 2022, "status": "continuing"}]))
    assert lines == ["not in your library — Severance (2022), continuing"]


def test_the_library_is_asked_before_the_metadata_provider():
    order = []

    def get(service, conf, path, params=None):
        order.append(path)
        return LIBRARY if path == "/api/v3/series" else []

    nas.airing(CONF, "great british bake off", get=get)
    assert order == ["/api/v3/series"]  # a hit never reaches the lookup


def test_airing_without_sonarr_configured_is_a_named_failure_not_a_crash():
    out = io.StringIO()
    env = {"RADARR_URL": "http://r", "RADARR_API_KEY": "k"}
    # `ssh=None` is not decoration: `ssh_config` reads the real filesystem, so
    # on a pod that has an ssh client and the sealed key mounted this call
    # discovers a live Sonarr and the test measures nothing. That is not
    # hypothetical -- it started happening on the bridge pod the day the key
    # was mounted there (Cycle 638).
    assert nas.main(["airing", "anything"], env=env, get=_library_get(), out=out, ssh=None) == 1
    assert "SONARR_URL" in out.getvalue()


def test_main_airing_joins_a_multi_word_term():
    seen = {}

    def get(service, conf, path, params=None):
        seen[path] = params
        return [] if path == "/api/v3/series" else []

    env = {"SONARR_URL": "http://s", "SONARR_API_KEY": "k"}
    nas.main(["airing", "great", "british", "bake", "off"], env=env, get=get, out=io.StringIO())
    assert seen["/api/v3/series/lookup"] == {"term": "great british bake off"}


# --- status -----------------------------------------------------------------


def test_status_reports_each_service_separately():
    def get(service, conf, path, params=None):
        if service == "radarr":
            raise nas.Unreachable("radarr did not answer: no route to host")
        return {"version": "4.0.16.2932"}

    lines, ok = nas.status(CONF, get=get)
    assert ok is False
    assert lines[0] == "ok              sonarr 4.0.16.2932 at http://s"
    assert lines[1] == "unreachable     radarr — radarr did not answer: no route to host"


def test_status_exits_0_only_when_everything_answered():
    env = {"SONARR_URL": "http://s", "SONARR_API_KEY": "k"}
    # `ssh=None` for the same reason as the test above: without it, a pod with
    # the SSH hop available configures both services from the NAS itself and
    # the "only Sonarr is configured" half of this test cannot fail.
    get = lambda *a, **k: {"version": "4.0"}
    assert nas.main(["status"], env=env, get=get, out=io.StringIO(), ssh=None) == 1
    env["RADARR_URL"], env["RADARR_API_KEY"] = "http://r", "k"
    assert nas.main(["status"], env=env, get=get, out=io.StringIO(), ssh=None) == 0


# --- the fixes the reviewer found ------------------------------------------


def test_the_window_starts_on_oslos_today_not_the_pods():
    # The pod runs UTC. At 23:30 Oslo on 27 Aug it is still 21:30 UTC on the
    # 27th in winter but 00:30 UTC on the 28th is the case that bites: a naive
    # date.today() would start the window on the wrong day.
    start, end = nas._window(3, today=dt.date(2026, 8, 27))
    assert (start, end) == ("2026-08-27", "2026-08-30")
    # 23:30 UTC on the 27th is 01:30 on the 28th in Oslo. The pod's own date
    # is still the 27th, so this is the hour where the two disagree.
    late = dt.datetime(2026, 8, 27, 23, 30, tzinfo=dt.timezone.utc)
    assert nas._window(1, now=late) == ("2026-08-28", "2026-08-29")


def test_a_movie_shows_its_earliest_release_not_the_first_field_present():
    # The fields are read in the order inCinemas, digitalRelease,
    # physicalRelease, so this row's *first present* date is not its earliest.
    line = nas._movie_line(
        {"title": "M", "year": 2026, "digitalRelease": "2026-08-29T12:00:00Z", "physicalRelease": "2026-08-20T12:00:00Z"}
    )
    assert line.startswith("2026-08-20"), line


def test_a_release_time_late_in_the_utc_day_lands_on_the_oslo_day():
    # 23:00Z on 29 Aug is 01:00 on the 30th in Oslo. Slicing the string to ten
    # characters would print the 29th while every other line here is Oslo.
    assert nas._to_oslo_date("2026-08-29T23:00:00Z") == "2026-08-30"
    assert nas._to_oslo_date(None) is None


def test_a_punctuation_only_term_does_not_match_the_whole_library():
    with pytest.raises(nas.NothingToSearchFor):
        nas.airing(CONF, "???", get=_library_get())
    out = io.StringIO()
    env = {"SONARR_URL": "http://s", "SONARR_API_KEY": "k"}
    assert nas.main(["airing", "???"], env=env, get=_library_get(), out=out) == 1
    assert "letter or a number" in out.getvalue()


def test_a_series_with_no_title_does_not_crash_the_line():
    def get(service, conf, path, params=None):
        return [{"title": None, "ended": True}] if path == "/api/v3/series" else []

    # A null title cannot match a real needle, so the lookup path is what runs;
    # the point is that neither branch raises a KeyError.
    assert nas.airing(CONF, "anything", get=get) == []


def test_a_window_shorter_than_a_day_is_refused_rather_than_silently_empty():
    with pytest.raises(SystemExit):
        nas.main(["calendar", "--days", "0"], env={"SONARR_URL": "http://s", "SONARR_API_KEY": "k"}, out=io.StringIO())


# --- the SSH transport (Cycle 631) -------------------------------------------
#
# The point of every test below is that nothing derived from the owner's typing
# reaches a shell, and that a failure over SSH is reported as the same kind of
# thing a failure over HTTP was.


class _FakeRun:
    """Stands in for `subprocess.run`, recording argv and stdin."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.calls = []

    def __call__(self, argv, input=None, capture_output=None, text=None, timeout=None):
        self.calls.append({"argv": argv, "input": input, "timeout": timeout})
        return types.SimpleNamespace(stdout=self.stdout, returncode=self.returncode, stderr=self.stderr)


SSH_HOP = {"host": "10.0.0.1", "user": "nova", "key": "/etc/nas-ssh/id_ed25519"}
SSH_CONF = {"url": "http://127.0.0.1:8989", "key": "deadbeef" * 4, "ssh": SSH_HOP}  # gitleaks:allow — fabricated


def test_the_remote_command_is_a_constant_and_carries_nothing_variable():
    run = _FakeRun(stdout='[{"title": "x"}]\n200')
    nas._get("sonarr", SSH_CONF, "/api/v3/series/lookup", {"term": "bake off; rm -rf /"}, run=run)
    argv = run.calls[0]["argv"]
    # The last argument is what a shell on the far side will parse. It must be
    # the same string on every call this module ever makes.
    assert argv[-1] == "curl --config -"
    joined = " ".join(argv)
    assert "rm -rf" not in joined
    assert "8989" not in joined  # not even the URL is on the command line
    assert SSH_CONF["key"] not in joined  # and the key is not visible in `ps`
    # All of it went in on stdin instead.
    assert "term=bake+off%3B+rm+-rf+%2F" in run.calls[0]["input"]
    assert f"header = \"X-Api-Key: {SSH_CONF['key']}\"" in run.calls[0]["input"]


def test_the_curl_config_never_asks_curl_to_follow_a_redirect():
    # `_NoRedirect` above exists because urllib copies the API key onto a
    # redirect target. curl's default is not to follow one, so the guarantee
    # here is that nothing ever turns that default off.
    assert "location" not in nas._curl_config("http://x/y", ("A: b",))


def test_no_config_entry_is_broken_by_a_raw_newline_inside_its_quotes():
    # A real newline inside a quoted value ends the entry, and curl then drops
    # every line after it in silence -- the first live run over SSH came back
    # with a real body, no status code and no API key header, which is what a
    # working service also looks like. `\n` here must be the two characters
    # curl unescapes, not the one character Python would.
    cfg = nas._curl_config("http://x/y", ("A: b",))
    for line in cfg.splitlines():
        assert line.count('"') in (0, 2), line
    assert '\\n%{http_code}' in cfg


def test_a_401_over_ssh_reads_as_a_refused_key_not_a_dead_service():
    run = _FakeRun(stdout="\n401")
    with pytest.raises(nas.Unreachable) as exc:
        nas._get("sonarr", SSH_CONF, "/api/v3/system/status", run=run)
    assert "refused the API key" in str(exc.value)


def test_a_200_that_is_not_json_is_not_an_answer():
    run = _FakeRun(stdout="<html>login</html>\n200")
    with pytest.raises(nas.Unreachable) as exc:
        nas._get("sonarr", SSH_CONF, "/api/v3/system/status", run=run)
    assert "not JSON" in str(exc.value)


def test_ssh_failing_to_connect_is_named_as_ssh_not_as_the_service():
    run = _FakeRun(returncode=255, stderr="Permission denied (publickey).")
    with pytest.raises(nas.Unreachable) as exc:
        nas._get("sonarr", SSH_CONF, "/api/v3/system/status", run=run)
    assert "ssh to 10.0.0.1 failed" in str(exc.value)
    assert "publickey" in str(exc.value)


def test_a_hang_is_a_timeout_rather_than_a_stuck_cycle():
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=25)

    with pytest.raises(nas.Unreachable) as exc:
        nas._get("sonarr", SSH_CONF, "/api/v3/system/status", run=hang)
    assert "did not answer within" in str(exc.value)


def test_ssh_config_needs_both_a_binary_and_a_key_and_says_no_otherwise():
    assert nas.ssh_config({}, exists=lambda p: True, which=lambda n: None) is None
    assert nas.ssh_config({}, exists=lambda p: False, which=lambda n: "/usr/bin/ssh") is None
    got = nas.ssh_config({}, exists=lambda p: True, which=lambda n: "/usr/bin/ssh")
    assert got == nas.SSH_DEFAULTS
    over = nas.ssh_config(
        {"NAS_SSH_HOST": "nas.local", "NAS_SSH_USER": "root", "NAS_SSH_KEY": "/k"},
        exists=lambda p: True,
        which=lambda n: "/usr/bin/ssh",
    )
    assert over == {"host": "nas.local", "user": "root", "key": "/k"}


def test_discover_key_reads_the_key_off_initialize_js():
    # The key below is fabricated. The first version of this test pasted the
    # real one off the NAS and the secret scan refused the branch, which is
    # the correct outcome: that key being readable by anyone on the LAN is a
    # decision the owner made about his LAN, and it is not the same decision
    # as putting it in a git history.
    run = _FakeRun(stdout="window.Sonarr = {\n  apiKey: '0123456789abcdef0123456789abcdef',\n};\n200")  # gitleaks:allow — fabricated
    assert nas.discover_key("sonarr", SSH_HOP, run=run) == "0123456789abcdef0123456789abcdef"  # gitleaks:allow — fabricated
    assert "8989/initialize.js" in run.calls[0]["input"]
    # No API key header on this one -- the whole point is that it needs none.
    assert "X-Api-Key" not in run.calls[0]["input"]


class _SeqRun:
    """Answers each call from a list, so the two initialize paths differ."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, argv, input=None, capture_output=None, text=None, timeout=None):
        self.calls.append({"argv": argv, "input": input, "timeout": timeout})
        stdout = self.replies[len(self.calls) - 1]
        return types.SimpleNamespace(stdout=stdout, returncode=0, stderr="")


def test_discover_key_reads_the_json_page_sonarr_4_serves():
    # Sonarr 4 / Radarr 6 answer 404 on /initialize.js and publish the key as
    # JSON instead. Measured live Cycle 659, minutes after upgrading both on
    # the NAS: every check in this package went UNREADABLE at once.
    run = _FakeRun(stdout='{\n  "apiKey": "0123456789abcdef0123456789abcdef",\n  "urlBase": ""\n}\n200')  # gitleaks:allow — fabricated
    assert nas.discover_key("radarr", SSH_HOP, run=run) == "0123456789abcdef0123456789abcdef"  # gitleaks:allow — fabricated
    assert "7878/initialize.json" in run.calls[0]["input"]


def test_discover_key_falls_back_to_the_old_js_page_on_a_404():
    # A box that has not been upgraded still answers only the old path, so the
    # newest spelling must not be the only one tried.
    run = _SeqRun([
        '{"title":"Not Found","status":404}\n404',
        "window.Sonarr = {\n  apiKey: '0123456789abcdef0123456789abcdef',\n};\n200",  # gitleaks:allow — fabricated
    ])
    assert nas.discover_key("sonarr", SSH_HOP, run=run) == "0123456789abcdef0123456789abcdef"  # gitleaks:allow — fabricated
    assert "8989/initialize.json" in run.calls[0]["input"]
    assert "8989/initialize.js" in run.calls[1]["input"]


def test_discover_key_stops_at_the_first_page_that_carries_a_key():
    run = _SeqRun(["{\"apiKey\": \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}\n200", "unused\n200"])  # gitleaks:allow — fabricated
    assert nas.discover_key("sonarr", SSH_HOP, run=run) == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # gitleaks:allow — fabricated
    assert len(run.calls) == 1


def test_discover_key_returns_none_when_neither_page_carries_a_key():
    run = _SeqRun(["\n404", "\n404"])
    assert nas.discover_key("sonarr", SSH_HOP, run=run) is None
    assert len(run.calls) == 2


def test_discover_key_returns_none_rather_than_guessing_when_the_page_is_locked():
    assert nas.discover_key("sonarr", SSH_HOP, run=_FakeRun(stdout="\n401")) is None
    assert nas.discover_key("sonarr", SSH_HOP, run=_FakeRun(stdout="no key here\n200")) is None
    assert nas.discover_key("plex", SSH_HOP, run=_FakeRun(stdout="\n200")) is None


def test_config_over_ssh_fills_in_loopback_and_a_discovered_key():
    run = _FakeRun(stdout="apiKey: 'aaaabbbbccccdddd'\n200")
    conf = nas.config({}, ssh=SSH_HOP, run=run)
    assert conf["sonarr"]["url"] == "http://127.0.0.1:8989"
    assert conf["radarr"]["url"] == "http://127.0.0.1:7878"
    assert conf["sonarr"]["key"] == "aaaabbbbccccdddd"
    assert conf["sonarr"]["ssh"] == SSH_HOP


def test_an_explicit_env_key_beats_discovery():
    run = _FakeRun(stdout="apiKey: 'aaaabbbbccccdddd'\n200")
    conf = nas.config({"SONARR_API_KEY": "mine"}, ssh=SSH_HOP, run=run)
    assert conf["sonarr"]["key"] == "mine"


def test_without_a_hop_config_is_exactly_what_it_always_was():
    assert nas.config({}, ssh=None) == {}
    conf = nas.config({"SONARR_URL": "nas:8989", "SONARR_API_KEY": "k"}, ssh=None)
    assert conf == {"sonarr": {"url": "http://nas:8989", "key": "k"}}


def test_a_value_that_could_break_out_of_the_quotes_is_refused_rather_than_written():
    for bad in ('http://x/"y', "http://x/\\y", "http://x/y\nurl = http://evil/"):
        with pytest.raises(ValueError):
            nas._curl_config(bad)
    with pytest.raises(ValueError):
        nas._curl_config("http://x/y", ('X-Api-Key: k"\nurl = http://evil/',))


# --- nzbget transport -------------------------------------------------------


def _hop_returning(*answers):
    """A fake `_fetch_over_ssh` that serves `(body, status)` in order."""
    calls = []
    queue = list(answers)

    def fetch(ssh, url, headers=(), run=None):
        calls.append((url, tuple(headers)))
        return queue.pop(0)

    fetch.calls = calls
    return fetch


def test_nzbget_read_only_refuses_saveconfig():
    # The write that would set an extension must not be reachable from here
    # at all, and the allowlist is the thing that guarantees it.
    assert "/jsonrpc/saveconfig" not in nas.NZBGET_READ_ONLY
    with pytest.raises(ValueError):
        nas._nzbget_url("/jsonrpc/saveconfig")


def test_nzbget_unlocked_reads_401_as_locked(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("", 401)))
    assert nas.nzbget_unlocked({"host": "h"}) is False


def test_nzbget_unlocked_reads_200_as_open(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("{}", 200)))
    assert nas.nzbget_unlocked({"host": "h"}) is True


def test_nzbget_unlocked_refuses_to_guess_at_any_other_status(monkeypatch):
    # A 500 or a captive portal's 302 is neither open nor locked, and calling
    # it "locked" is the answer that hides an open control interface.
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("", 500)))
    with pytest.raises(nas.Unreachable):
        nas.nzbget_unlocked({"host": "h"})


def test_nzbget_config_folds_names_and_sends_basic_auth(monkeypatch):
    fetch = _hop_returning(('{"result":[{"Name":"ControlIp","Value":"0.0.0.0"}]}', 200))
    monkeypatch.setattr(nas, "_fetch_over_ssh", fetch)
    conf = nas.nzbget_config({"host": "h"}, ("admin", "pw"))
    # Folded, because the running box serves `ControlIp` where the docs say
    # `ControlIP` and a check that misses one letter reads as a clean check.
    assert conf == {"controlip": "0.0.0.0"}
    assert fetch.calls[0][1] == ("Authorization: Basic YWRtaW46cHc=",)


def test_nzbget_config_reports_a_refused_credential_as_unreachable(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("", 401)))
    with pytest.raises(nas.Unreachable, match="refused the control credential"):
        nas.nzbget_config({"host": "h"}, ("admin", "pw"))


def test_nzbget_config_refuses_a_200_that_is_not_a_config(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("<html>login</html>", 200)))
    with pytest.raises(nas.Unreachable):
        nas.nzbget_config({"host": "h"}, ("admin", "pw"))


def test_nzbget_credential_needs_both_halves():
    assert nas.nzbget_credential({"NZBGET_USER": "admin", "NZBGET_PASS": "pw"}) == ("admin", "pw")
    assert nas.nzbget_credential({"NZBGET_USER": "admin"}) is None
    assert nas.nzbget_credential({"NZBGET_PASS": "pw"}) is None
    assert nas.nzbget_credential({}) is None


def test_nzbget_unlocked_sends_no_credential_at_all(monkeypatch):
    # The whole value of this probe is that it needs nothing handed in, so a
    # credential leaking into it would make it silently stop measuring "can
    # anyone get in" and start measuring "can we get in".
    fetch = _hop_returning(("", 401))
    monkeypatch.setattr(nas, "_fetch_over_ssh", fetch)
    nas.nzbget_unlocked({"host": "h"})
    url, headers = fetch.calls[0]
    assert headers == ()
    assert "@" not in url


# --- Plex ------------------------------------------------------------------

PLEX_IDENTITY = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<MediaContainer size="0" apiVersion="0.2.0" claimed="1" '
    'machineIdentifier="43313b331031cf9a62524719a06e0ef3116ddd49" '
    'version="1.41.6.9685-d301f511a">\n</MediaContainer>\n'
)


def test_plex_read_only_refuses_anything_but_identity():
    # Everything else on :32400 needs a token and reaches his library. The
    # allowlist is what guarantees this module cannot ask for any of it.
    assert nas.PLEX_READ_ONLY == {"/identity"}
    for path in ("/library/sections", "/:/prefs", "/status/sessions"):
        with pytest.raises(ValueError):
            nas._plex_url(path)


def test_plex_version_reads_the_attribute_off_identity(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning((PLEX_IDENTITY, 200)))
    assert nas.plex_version({"host": "h"}) == "1.41.6.9685-d301f511a"


def test_plex_version_sends_no_credential_and_asks_only_for_identity(monkeypatch):
    fetch = _hop_returning((PLEX_IDENTITY, 200))
    monkeypatch.setattr(nas, "_fetch_over_ssh", fetch)
    nas.plex_version({"host": "h"})
    url, headers = fetch.calls[0]
    assert headers == ()
    assert url == f"http://127.0.0.1:{nas.PLEX_PORT}/identity"


def test_plex_version_refuses_a_non_200(monkeypatch):
    # A Plex that wants a token, or a proxy in front of it, is unreadable --
    # never a version that happens to be current.
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("", 401)))
    with pytest.raises(nas.Unreachable, match="401"):
        nas.plex_version({"host": "h"})


def test_plex_version_refuses_a_200_that_is_not_xml(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("<html>login", 200)))
    with pytest.raises(nas.Unreachable, match="not XML"):
        nas.plex_version({"host": "h"})


def test_plex_version_refuses_xml_with_no_version_attribute(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("<MediaContainer/>", 200)))
    with pytest.raises(nas.Unreachable, match="no `version` attribute"):
        nas.plex_version({"host": "h"})


COMPOSE_SAMPLE = """version: "2.1"
services:
  nzbget:
    image: lscr.io/linuxserver/nzbget:latest
    environment:
      - PUID=1000
      - TZ=Europe/London
      - NZBGET_USER=admin #optional
      - NZBGET_PASS=potatopass #optional
"""


def test_compose_credential_strips_the_trailing_comment():
    # `- NZBGET_PASS=potatopass #optional` is what his file actually says. A
    # split on `=` alone reads the password as `potatopass #optional`, which
    # authenticates as nobody and looks exactly like a wrong password.
    assert nas._credential_from_compose(COMPOSE_SAMPLE) == ("admin", "potatopass")


def test_compose_credential_needs_both_halves():
    only_user = COMPOSE_SAMPLE.replace("      - NZBGET_PASS=potatopass #optional\n", "")
    assert nas._credential_from_compose(only_user) is None
    assert nas._credential_from_compose("services:\n  nzbget:\n") is None


def test_nzbget_credential_prefers_the_environment_over_the_nas():
    # A credential handed in deliberately beats one scraped off his disk, and
    # it must not cost an ssh call to find that out.
    calls = []

    def run(*a, **k):
        calls.append(a)
        raise AssertionError("should not have reached the NAS")

    got = nas.nzbget_credential({"NZBGET_USER": "handed", "NZBGET_PASS": "in"},
                                ssh=SSH_STUB, run=run)
    assert got == ("handed", "in")
    assert calls == []


def test_nzbget_credential_reads_the_compose_file_over_the_hop():
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout=COMPOSE_SAMPLE, stderr="")

    assert nas.nzbget_credential({}, ssh=SSH_STUB, run=run) == ("admin", "potatopass")
    # The remote command is a constant -- nothing variable crosses to the far
    # side, the same rule `_run_ssh` is built on.
    assert seen["argv"][-1] == f"cat {nas.NZBGET_COMPOSE_FILE}"


def test_nzbget_credential_is_none_when_the_hop_fails():
    # stdout deliberately carries a parseable file: a failed ssh that still
    # printed something is the case where ignoring the exit status hands back a
    # credential that was never really read. An empty stdout would make this
    # test pass whether or not the status is checked at all.
    def run(argv, **kwargs):
        return types.SimpleNamespace(returncode=255, stdout=COMPOSE_SAMPLE,
                                     stderr="ssh: connect to host nas.example port 22: refused")

    assert nas.nzbget_credential({}, ssh=SSH_STUB, run=run) is None


def test_nzbget_credential_without_a_hop_never_shells_out():
    assert nas.nzbget_credential({}, ssh=None) is None


def test_calendar_comes_back_in_date_order_not_text_order():
    # The bug this replaces: `main` printed `sorted(lines)`, comparing the
    # rendered text, so `07 Oct` sorted above `16 Sep`. Seven days hides it
    # because the rows share a month; sixty days made his real calendar
    # unreadable. The API is free to return rows in any order, so these come
    # back deliberately shuffled.
    def get(service, conf, path, params=None):
        if service == "radarr":
            return []
        return [
            _ep("Slow Horses", 6, 4, "2026-10-07T04:00:00Z"),
            _ep("Slow Horses", 6, 1, "2026-09-16T04:00:00Z"),
            _ep("Grey's Anatomy", 23, 1, "2026-10-16T01:00:00Z"),
            _ep("Slow Horses", 6, 2, "2026-09-23T04:00:00Z"),
        ]

    lines, failures = nas.calendar(CONF, days=60, get=get, today=dt.date(2026, 8, 30))
    assert failures == []
    assert [line.split("  ")[0] for line in lines] == [
        "16 Sep 06:00",
        "23 Sep 06:00",
        "07 Oct 06:00",
        "16 Oct 03:00",
    ]


def test_a_movie_sorts_and_prints_the_release_that_put_it_in_the_window():
    # His live calendar, 30 August 2026: Radarr returned `Hadestown` for a
    # window starting that day and the row printed `2026-07-23`, five weeks
    # earlier -- the cinema date, which is not why Radarr matched it.
    def get(service, conf, path, params=None):
        if service == "sonarr":
            return [_ep("Slow Horses", 6, 1, "2026-09-16T04:00:00Z")]
        return [
            {
                "title": "Hadestown: The Musical",
                "year": 2026,
                "inCinemas": "2026-07-23T00:00:00Z",
                "digitalRelease": "2026-09-30T00:00:00Z",
                "hasFile": True,
            }
        ]

    lines, failures = nas.calendar(CONF, days=60, get=get, today=dt.date(2026, 8, 30))
    assert failures == []
    assert lines[0].startswith("16 Sep 06:00")
    assert lines[1] == "2026-09-30  Hadestown: The Musical (2026) (have it)"


def test_a_row_with_no_air_date_sorts_last_rather_than_first():
    # An empty key is the smallest string there is, so a row with nothing to
    # sort on would head the page it is least useful on.
    def get(service, conf, path, params=None):
        if service == "radarr":
            return []
        return [
            _ep("No Date", 1, 1, None),
            _ep("Has A Date", 1, 1, "2026-09-16T04:00:00Z"),
        ]

    lines, _ = nas.calendar(CONF, days=60, get=get, today=dt.date(2026, 8, 30))
    assert "Has A Date" in lines[0]
    assert "No Date" in lines[1]


def _ep(series, season, number, air_date_utc):
    return {
        "series": {"title": series},
        "seasonNumber": season,
        "episodeNumber": number,
        "title": "TBA",
        "airDateUtc": air_date_utc,
        "hasFile": False,
    }


def test_a_movie_sorts_above_the_same_days_episodes_not_two_hours_into_it():
    # Radarr's dates arrive already folded to an Oslo day. Re-parsing one as a
    # timestamp reads its midnight as UTC and lands it at 02:00 Oslo, which
    # sorts a film below an episode that airs at 01:00 on the same day.
    assert nas._sort_key("2026-10-20") == "2026-10-20"
    assert nas._sort_key("2026-10-20") < nas._sort_key("2026-10-20T00:30:00Z")
    # 23:00Z on the 19th is 01:00 Oslo on the 20th, so it belongs *after* the
    # film with no time on the 20th -- the day is the Oslo day, not the UTC one.
    assert nas._sort_key("2026-10-20") < nas._sort_key("2026-10-19T23:00:00Z")
    assert nas._sort_key("2026-10-19T18:00:00Z") < nas._sort_key("2026-10-20")


# --- bazarr -----------------------------------------------------------------
#
# The fifth app on that box, added Cycle 661. Two seams and one boundary: the
# key comes off an unauthenticated front page, the version comes out of a
# `{"data": {...}}` envelope, and `/api/system/settings` -- which that same key
# opens, and which returns his subtitle providers' passwords in plaintext --
# must not be reachable from this module at all.

#: A fabricated key, not the one on his box. Bazarr publishes its real key in
#: the HTML of its own unauthenticated front page -- which is the finding this
#: module reports -- and putting that string in a test fixture would commit a
#: live credential to a public-history repo on top of it. Cycle 661 did
#: exactly that and the secret scan caught it before the merge.
BAZARR_INDEX = (
    '<!DOCTYPE html><html><head><title>Bazarr</title></head><body>'
    '<script>window.Bazarr = JSON.parse(`{"apiKey": '
    '"deadbeefcafe0123deadbeefcafe0123", "baseUrl": "", "canUpdate": false}`);'  # gitleaks:allow -- fabricated, not his key
    '</script></body></html>'
)

BAZARR_RELEASES = (
    '{"data": ['
    '{"name": "v1.6.1-beta.34", "date": "2026-08-20", "prerelease": true, "current": false},'
    '{"name": "v1.6.0", "date": "2026-07-04", "prerelease": false, "current": false},'
    '{"name": "v1.5.6", "date": "2026-02-26", "prerelease": false, "current": false}'
    ']}'
)


def test_bazarr_read_only_refuses_the_settings_endpoint():
    # The one that matters by its absence. `/api/system/settings` answers 200
    # to the key this module can read, and the body carries credentials.
    with pytest.raises(ValueError):
        nas._bazarr_url("/api/system/settings")


def test_bazarr_read_only_refuses_the_status_endpoint():
    # Not a security boundary like the one above -- `/api/system/status` does
    # not answer on that box, and a path this module cannot name is a path no
    # later edit can quietly start waiting on.
    with pytest.raises(ValueError):
        nas._bazarr_url("/api/system/status")


def test_bazarr_key_comes_off_the_unauthenticated_front_page(monkeypatch):
    fetch = _hop_returning((BAZARR_INDEX, 200))
    monkeypatch.setattr(nas, "_fetch_over_ssh", fetch)
    assert nas.bazarr_key({"host": "h"}) == "deadbeefcafe0123deadbeefcafe0123"  # gitleaks:allow -- fabricated, not his key
    url, headers = fetch.calls[0]
    assert headers == ()  # the page is served to anyone; that is the finding
    assert url == f"http://127.0.0.1:{nas.BAZARR_PORT}/"


def test_bazarr_key_missing_raises_rather_than_returning_none(monkeypatch):
    # `discover_key` answers None because a missing key there is one of two
    # *arr apps going unconfigured. Here it has to be distinguishable from an
    # unreadable release list, so it raises.
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("<html>login</html>", 200)))
    with pytest.raises(nas.Unreachable, match="no `apiKey`"):
        nas.bazarr_key({"host": "h"})


def test_bazarr_key_refuses_a_non_200(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("", 500)))
    with pytest.raises(nas.Unreachable, match="500"):
        nas.bazarr_key({"host": "h"})


def test_bazarr_standing_reads_the_release_list_and_sends_the_key(monkeypatch):
    fetch = _hop_returning((BAZARR_INDEX, 200), (BAZARR_RELEASES, 200))
    monkeypatch.setattr(nas, "_fetch_over_ssh", fetch)
    newest, date, current, listed = nas.bazarr_standing({"host": "h"}, env={})
    # The beta row is dropped: being behind a prerelease is not a finding.
    assert (newest, date, current, listed) == ("v1.6.0", "2026-07-04", False, 2)
    url, headers = fetch.calls[1]
    assert url == f"http://127.0.0.1:{nas.BAZARR_PORT}/api/system/releases"
    assert headers == ("X-API-KEY: deadbeefcafe0123deadbeefcafe0123",)  # gitleaks:allow -- fabricated, not his key


def test_bazarr_standing_is_current_when_a_stable_row_is_flagged(monkeypatch):
    body = ('{"data": [{"name": "v1.6.0", "date": "2026-07-04", '
            '"prerelease": false, "current": true}]}')
    monkeypatch.setattr(nas, "_fetch_over_ssh",
                        _hop_returning((BAZARR_INDEX, 200), (body, 200)))
    assert nas.bazarr_standing({"host": "h"}, env={})[2] is True


def test_bazarr_standing_prefers_a_key_from_the_environment(monkeypatch):
    # The day he puts a login on it, the front page stops being the path.
    fetch = _hop_returning((BAZARR_RELEASES, 200))
    monkeypatch.setattr(nas, "_fetch_over_ssh", fetch)
    nas.bazarr_standing({"host": "h"}, env={"BAZARR_API_KEY": "handed-in"})
    assert len(fetch.calls) == 1  # the front page was never asked for
    assert fetch.calls[0][1] == ("X-API-KEY: handed-in",)


def test_bazarr_standing_refuses_a_non_200(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh",
                        _hop_returning((BAZARR_INDEX, 200), ("", 401)))
    with pytest.raises(nas.Unreachable, match="401"):
        nas.bazarr_standing({"host": "h"}, env={})


def test_bazarr_standing_refuses_a_200_that_is_not_json(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh",
                        _hop_returning((BAZARR_INDEX, 200), ("<html>login", 200)))
    with pytest.raises(nas.Unreachable, match="not JSON"):
        nas.bazarr_standing({"host": "h"}, env={})


def test_bazarr_standing_refuses_json_with_no_data_list(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh",
                        _hop_returning((BAZARR_INDEX, 200), ('{"data": {}}', 200)))
    with pytest.raises(nas.Unreachable, match="no `data` list"):
        nas.bazarr_standing({"host": "h"}, env={})


def test_bazarr_standing_refuses_a_list_of_prereleases_only(monkeypatch):
    # An all-beta window carries no published release to be behind, and
    # answering "current" over it would be a verdict from no evidence.
    body = '{"data": [{"name": "v1.6.1-beta.34", "prerelease": true, "current": true}]}'
    monkeypatch.setattr(nas, "_fetch_over_ssh",
                        _hop_returning((BAZARR_INDEX, 200), (body, 200)))
    with pytest.raises(nas.Unreachable, match="no published release"):
        nas.bazarr_standing({"host": "h"}, env={})


def test_bazarr_standing_refuses_a_row_with_no_name(monkeypatch):
    body = '{"data": [{"date": "2026-07-04", "prerelease": false, "current": false}]}'
    monkeypatch.setattr(nas, "_fetch_over_ssh",
                        _hop_returning((BAZARR_INDEX, 200), (body, 200)))
    with pytest.raises(nas.Unreachable, match="no `name`"):
        nas.bazarr_standing({"host": "h"}, env={})


def test_bazarr_is_in_the_media_inventory_and_not_in_services():
    # `SERVICES` is the *arr shape; `MEDIA_SERVICES` is what runs on the box.
    assert "bazarr" in nas.MEDIA_SERVICES
    assert "bazarr" not in nas.SERVICES
    assert len(nas.MEDIA_SERVICES) == 5



# --- Tautulli ---------------------------------------------------------------


def _tautulli_body(rows, result="success"):
    return json.dumps({"response": {"result": result, "message": None, "data": rows}})


def test_tautulli_notifiers_reads_the_configured_agents(monkeypatch):
    rows = [{"id": 1, "agent_name": "scripts", "agent_label": "Script"}]
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning((_tautulli_body(rows), 200)))
    assert nas.tautulli_notifiers(SSH_STUB, "deadbeef") == rows


def test_tautulli_a_refused_key_is_unreachable_and_not_an_empty_list(monkeypatch):
    """Measured on the box: a wrong key answers 401, and 401 must not read as "no agents".

    This is the failure `nas_watch` cannot survive -- a confident "nothing on
    the NAS runs a script on an event" produced by never having been let in.
    """
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning(("", 401)))
    with pytest.raises(nas.Unreachable):
        nas.tautulli_notifiers(SSH_STUB, "wrong")


def test_tautulli_an_error_envelope_is_unreachable_even_at_200(monkeypatch):
    """Tautulli answers its own errors with HTTP 200 and `result: error`.

    So the status code alone is not the check: without reading `result` an
    error envelope carries `data: null` straight through as no agents.
    """
    monkeypatch.setattr(nas, "_fetch_over_ssh",
                        _hop_returning((_tautulli_body(None, result="error"), 200)))
    with pytest.raises(nas.Unreachable):
        nas.tautulli_notifiers(SSH_STUB, "deadbeef")


def test_tautulli_a_non_list_payload_is_unreachable(monkeypatch):
    monkeypatch.setattr(nas, "_fetch_over_ssh", _hop_returning((_tautulli_body({}), 200)))
    with pytest.raises(nas.Unreachable):
        nas.tautulli_notifiers(SSH_STUB, "deadbeef")


def test_tautulli_key_prefers_the_environment_over_the_box(monkeypatch):
    monkeypatch.setattr(nas, "_run_ssh_fixed",
                        lambda *a, **k: pytest.fail("the box must not be asked when the env has it"))
    assert nas.tautulli_key(SSH_STUB, env={"TAUTULLI_API_KEY": "from-env"}) == "from-env"


def test_tautulli_key_from_the_config_file_is_stripped_of_its_quotes(monkeypatch):
    monkeypatch.setattr(nas, "_run_ssh_fixed", lambda *a, **k: '"abc123"\n')
    assert nas.tautulli_key(SSH_STUB, env={}) == "abc123"


def test_tautulli_an_absent_key_is_unreachable_rather_than_none(monkeypatch):
    """`nas_watch` has to tell "no key" from "no notifier"; a None collapses them."""
    monkeypatch.setattr(nas, "_run_ssh_fixed", lambda *a, **k: "\n")
    with pytest.raises(nas.Unreachable):
        nas.tautulli_key(SSH_STUB, env={})


def test_only_a_declared_command_may_run_on_the_nas():
    """`_run_ssh` keeps the remote command a literal; the fixed runner must too."""
    with pytest.raises(ValueError):
        nas._run_ssh_fixed(SSH_STUB, "rm -rf /volume1", run=lambda *a, **k: pytest.fail("never"))


def test_the_tautulli_url_refuses_a_command_that_is_not_read_only():
    with pytest.raises(ValueError):
        nas._tautulli_url("delete_notifier", "deadbeef")
