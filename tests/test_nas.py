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

import pytest

from tools import nas


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
    source = pathlib.Path(nas.__file__).read_text()
    body = source.split("READ_ONLY = {", 1)[1].split("}", 1)[1]
    for path in nas.READ_ONLY:
        assert f'"{path}"' in body, f"{path} is allowed but never called"


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


def test_config_needs_both_a_url_and_a_key():
    assert nas.config({"SONARR_URL": "http://nas:8989"}) == {}
    assert nas.config({"SONARR_API_KEY": "k"}) == {}
    assert nas.config({"SONARR_URL": "http://nas:8989", "SONARR_API_KEY": "k"}) == {
        "sonarr": {"url": "http://nas:8989", "key": "k"}
    }


def test_config_accepts_a_bare_host_and_trims_a_trailing_slash():
    assert nas.config({"RADARR_URL": "nas:7878/", "RADARR_API_KEY": "k"})["radarr"]["url"] == "http://nas:7878"


def test_one_configured_service_is_enough():
    conf = nas.config({"RADARR_URL": "http://nas:7878", "RADARR_API_KEY": "k"})
    assert set(conf) == {"radarr"}


def test_main_with_nothing_configured_names_the_four_values_and_exits_1():
    out = io.StringIO()
    assert nas.main(["status"], env={}, out=out) == 1
    text = out.getvalue()
    for value in ("SONARR_URL", "SONARR_API_KEY", "RADARR_URL", "RADARR_API_KEY"):
        assert value in text
    assert "Tailscale" in text  # it says whose step is missing, not just what


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
    assert nas.main(["airing", "anything"], env=env, get=_library_get(), out=out) == 1
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
    assert nas.main(["status"], env=env, get=lambda *a, **k: {"version": "4.0"}, out=io.StringIO()) == 1
    env["RADARR_URL"], env["RADARR_API_KEY"] = "http://r", "k"
    assert nas.main(["status"], env=env, get=lambda *a, **k: {"version": "4.0"}, out=io.StringIO()) == 0


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
