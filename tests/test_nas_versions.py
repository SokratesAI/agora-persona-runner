"""Tests for `tools.nas_versions`.

Nothing here touches the real NAS or GitHub: `ssh` stands in for the hop,
`get` for the two apps' status endpoints, and `latest_release` for upstream.

The exit contract is what every test protects, in both directions, because a
wrong answer here is worse than no check at all. A major behind must exit 2
even though everything was readable; a status endpoint that could not be read
must exit 1 rather than being skipped silently, which is the failure that
would let a stale app disappear from the sweep; an upstream release this pod
cannot read must exit 1 rather than being cleared, because an unjudged version
must not look like a current one; and a minor gap must exit 0 and still be
printed, because a check that fires on every point release stops being read.
"""

import datetime
import io

from tools import nas, nas_versions


HOP = {"host": "nas.example", "user": "nova", "key": "/etc/nas-ssh/id_ed25519"}

NOW = datetime.datetime(2026, 8, 30, tzinfo=datetime.timezone.utc)


def _conf(*services):
    return {name: {"url": f"http://127.0.0.1:{8989 + i}", "key": "k", "ssh": HOP}
            for i, name in enumerate(services)}


def _get_returning(by_service):
    """A fake `nas._get` serving a canned status object per service."""
    def get(service, conf, path, **kwargs):
        assert path == nas_versions.STATUS_PATH
        answer = by_service[service]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return get


def _upstream(by_repo):
    def latest_release(repo, **kwargs):
        tag = by_repo.get(repo)
        return (tag, None) if tag else (None, f"no release for {repo}")
    return latest_release


#: What Plex answers with unless a test says otherwise: on its train, so no
#: test that is about sonarr and radarr accidentally depends on Plex's verdict.
PLEX_OK = "1.43.3.10896-cb3ebc72d"


def _plex(running=PLEX_OK, latest=PLEX_OK, rows=3, why_not=None):
    """Fakes for the two Plex seams: `/identity` and the vendor manifest."""
    def version(hop, **kwargs):
        if isinstance(running, Exception):
            raise running
        return running
    def upstream(**kwargs):
        return (latest, rows, why_not)
    return version, upstream


#: What Bazarr answers with unless a test says otherwise: running its own
#: newest published release, so no test about the other four depends on it.
#: The tuple is `(newest, date, current, listed)`.
BAZARR_OK = ("v1.6.0", "2026-07-04", True, 5)


def _bazarr(standing=BAZARR_OK):
    def read(hop, **kwargs):
        if isinstance(standing, Exception):
            raise standing
        return standing
    return read


def _run(services, statuses, upstream, ssh=HOP, plex=None, bazarr=None):
    out = io.StringIO()
    plex_version, plex_latest = plex if plex else _plex()
    code = nas_versions.report(
        out=out,
        ssh=ssh,
        get=_get_returning(statuses),
        latest_release=_upstream(upstream),
        now=NOW,
        env={},
        run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no shell in these tests")),
        plex_running=plex_version,
        plex_latest=plex_latest,
        bazarr_standing=bazarr if bazarr else _bazarr(),
    )
    return code, out.getvalue()


def _status(version, build="2024-01-01T00:00:00Z"):
    return {"version": version, "buildTime": build}


def _patch_config(monkeypatch, conf):
    monkeypatch.setattr(nas, "config", lambda env=None, ssh=None, run=None: conf)


def test_major_behind_raises(monkeypatch):
    """The live shape as of Cycle 642: both apps a major behind, exit 2."""
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("3.0.9.1549", "2022-08-06T15:55:48Z"),
         "radarr": _status("4.3.2.6857", "2023-01-04T00:24:06Z")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
    )
    assert code == 2
    assert "MAJOR VERSION BEHIND" in text
    assert "sonarr 3.0.9.1549" in text and "radarr 4.3.2.6857" in text
    # The age is context and has to actually appear, or the strongest sentence
    # in the report is one nobody reads.
    assert "built 1484 day(s) ago" in text
    assert "Judged the running version of 2 service(s) of 2 plus plex" in text


def test_a_minor_gap_is_printed_and_does_not_raise(monkeypatch):
    """A minor release must not turn this red -- that is the whole threshold.

    Without this the check fires on projects that ship continuously, and a
    check that is always red is the same as no check. `4.0.x` against `4.2.x`
    is the case that pins the threshold at *major*: my first version of this
    test used `4.0.14` against `4.0.19`, which is a patch gap, so widening the
    rule to `("major", "minor")` left the whole suite green. A threshold test
    has to sit on the boundary it is claiming.
    """
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.14.2939"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.2.0.2979", "Radarr/Radarr": "v6.3.0.10514"},
    )
    assert code == 0
    assert "MAJOR VERSION BEHIND" not in text
    assert "NOT A MAJOR BEHIND" in text
    assert "sonarr 4.0.14.2939" in text
    assert "NO APP IS BEHIND ITS OWN RELEASE TRAIN on 2 service(s)" in text


def test_a_patch_gap_is_printed_and_does_not_raise(monkeypatch):
    """The other side of the same threshold, one step in."""
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10500")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.1.10514"},
    )
    assert code == 0
    assert "NOT A MAJOR BEHIND" in text


def test_unreadable_status_exits_one_and_is_not_skipped(monkeypatch):
    """An app that could not be read must never fall out of the sweep quietly."""
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"),
         "radarr": nas.Unreachable("connection refused")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
    )
    assert code == 1
    assert "CANNOT JUDGE" in text
    assert "radarr" in text and "connection refused" in text
    # One of two judged: a partial sweep must never read as a clean one.
    assert "Judged the running version of 1 service(s) of 2" in text
    # And the headline that says everything is fine must not be printed beside
    # a CANNOT JUDGE. Weakening that guard to `if not behind:` left all
    # thirteen tests green; the exit code held and only the prose lied, and
    # the prose is what gets read. My reviewer found it.
    assert "NO APP IS A MAJOR BEHIND" not in text


def test_status_that_is_not_an_object_exits_one(monkeypatch):
    """A login page can answer 200 with JSON that is not a status object."""
    _patch_config(monkeypatch, _conf("sonarr"))
    code, text = _run(("sonarr",), {"sonarr": ["not", "a", "status"]},
                      {"Sonarr/Sonarr": "v4.0.19.2979"})
    assert code == 1
    assert "not a status object" in text


def test_missing_version_field_exits_one(monkeypatch):
    """No `version` is not version zero and must not be compared."""
    _patch_config(monkeypatch, _conf("sonarr"))
    code, text = _run(("sonarr",), {"sonarr": {"buildTime": "2024-01-01T00:00:00Z"}},
                      {"Sonarr/Sonarr": "v4.0.19.2979"})
    assert code == 1
    assert "carried no `version` field" in text


def test_unreadable_upstream_exits_one_rather_than_clearing(monkeypatch):
    """An upstream this pod cannot read must not look like a current version.

    This is the failure the check exists to avoid inverting: a GitHub outage
    would otherwise clear a four-year-old app.
    """
    _patch_config(monkeypatch, _conf("sonarr"))
    code, text = _run(("sonarr",), {"sonarr": _status("3.0.9.1549")}, {})
    assert code == 1
    assert "CANNOT JUDGE" in text
    assert "upstream Sonarr/Sonarr could not be read" in text
    assert "MAJOR VERSION BEHIND" not in text


def test_an_uncomparable_version_string_exits_one_rather_than_clearing(monkeypatch):
    """`nightly` is not a version and must not be filed as "not a major behind".

    `pin_drift.gap` answers None for a string with no leading number. My first
    draft tested `== "major"` and sent everything else to the cleared bucket,
    so an app on a `nightly` tag came out looking current. I found this by
    running `gap` over the shapes it would actually see rather than by
    re-reading the code.
    """
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(("sonarr", "radarr"),
                      {"sonarr": _status("nightly"), "radarr": _status("6.3.0.10514")},
                      {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"})
    assert code == 1
    assert "CANNOT JUDGE" in text
    assert "cannot be compared" in text
    # It is not counted as judged either -- a partial sweep must say so.
    assert "Judged the running version of 1 service(s) of 2" in text


def test_a_behind_app_still_raises_when_another_is_unreadable(monkeypatch):
    """A real finding must outrank an incomplete sweep, not hide behind it."""
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("3.0.9.1549"), "radarr": nas.Unreachable("refused")},
        {"Sonarr/Sonarr": "v4.0.19.2979"},
    )
    assert code == 2
    assert "MAJOR VERSION BEHIND" in text and "CANNOT JUDGE" in text


def test_no_hop_says_so_and_does_not_raise(monkeypatch):
    """On a pod with no SSH hop this judges nothing and claims nothing."""
    _patch_config(monkeypatch, _conf("sonarr"))
    code, text = _run(("sonarr",), {}, {}, ssh=None)
    assert code == 0
    assert "CANNOT SEE FROM THIS POD" in text
    assert "Judged 0 service(s)" in text


def test_unconfigured_services_exit_one(monkeypatch):
    """A hop with nothing behind it is unreadable, not an empty NAS."""
    _patch_config(monkeypatch, {})
    code, text = _run((), {}, {})
    assert code == 1
    assert "SERVICES UNREADABLE" in text
    assert "Judged 0 of 2 service(s)" in text


def test_build_age_days_reads_a_z_stamp_and_shrugs_at_junk():
    assert nas_versions.build_age_days("2022-08-06T15:55:48Z", now=NOW) == 1484
    assert nas_versions.build_age_days("2026-08-20T00:00:00+00:00", now=NOW) == 10
    # A stamp this cannot parse yields None so the age is simply not printed;
    # the age is context and must never change a status.
    assert nas_versions.build_age_days("not a date", now=NOW) is None
    assert nas_versions.build_age_days(None, now=NOW) is None


def test_a_service_that_never_configured_is_unjudged_not_absent(monkeypatch):
    """`nas.config` drops a service whose key discovery failed -- silently.

    One transient failure fetching an app's `/initialize.js` removes it from
    the mapping entirely. Before my reviewer found this, the loop iterated
    what came back and divided by it, so the four-year-old Sonarr this file
    exists to find disappeared and the report exited 0 saying "1 of 1".
    """
    _patch_config(monkeypatch, _conf("radarr"))
    code, text = _run(("radarr",), {"radarr": _status("6.3.0.10514")},
                      {"Radarr/Radarr": "v6.3.0.10514"})
    assert code == 1
    assert "CANNOT JUDGE" in text
    assert "sonarr" in text and "never asked" in text
    assert "Judged the running version of 1 service(s) of 2" in text
    assert "NO APP IS A MAJOR BEHIND" not in text


def test_an_unreadable_build_date_says_so_in_the_report(monkeypatch):
    """A missing `buildTime` must not read as "built today".

    `build_age_days` was tested directly and `_age_phrase` was not reached
    through `report()` at all, so replacing "build date unreadable" with
    "built 0 day(s) ago" left the suite green -- the exact inverse of the
    finding, on the line that carries the strongest sentence in the report.
    """
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": {"version": "3.0.9.1549"}, "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
    )
    assert code == 2
    assert "build date unreadable" in text
    assert "built 0 day(s) ago" not in text


def test_the_status_path_is_one_nas_will_actually_fetch():
    """Every test here fakes `get`, so nothing else pins this.

    `nas._get` raises ValueError -- not Unreachable, so uncaught -- on a path
    outside `READ_ONLY`. If that list were ever trimmed the check would die
    with a traceback while this whole suite stayed green.
    """
    assert nas_versions.STATUS_PATH in nas.READ_ONLY


def test_upstream_table_covers_every_service_nas_knows_about():
    """A new service in `tools.nas` must not silently fall out of this sweep.

    Without this, adding one lands it in `CANNOT JUDGE` forever with a message
    about a mapping, which reads like a NAS problem and is a code problem.
    """
    assert set(nas.SERVICES) == set(nas_versions.UPSTREAM)


# --- Plex ------------------------------------------------------------------
#
# Plex is judged against a different upstream and on a different field, so
# every one of these pins the boundary rather than a comfortable case. The
# rule under test: Plex names its release series by the major.minor pair, so a
# minor gap is a train behind and raises, and only a patch is printed.


def test_plex_a_minor_behind_raises(monkeypatch):
    """The live shape as of Cycle 645: 1.41 against 1.43, exit 2.

    This sits on the boundary on purpose. `1.41.6` against `1.43.3` is a
    *minor* gap -- the case that separates Plex's rule from sonarr's and
    radarr's, where a minor is printed and cleared.
    """
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
        plex=_plex(running="1.41.6.9685-d301f511a", latest="1.43.3.10896-cb3ebc72d"),
    )
    assert code == 2
    assert "PLEX IS BEHIND ITS RELEASE TRAIN" in text
    assert "plex 1.41.6.9685-d301f511a against plex.tv 1.43.3.10896-cb3ebc72d" in text
    assert "newest of 3 Synology row(s)" in text
    # The two *arr apps were current here, so the 2 came from Plex alone.
    assert "MAJOR VERSION BEHIND" not in text


def test_plex_a_patch_behind_is_printed_and_does_not_raise(monkeypatch):
    """The other side of the same boundary: 1.43.3 against 1.43.5 is a patch."""
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
        plex=_plex(running="1.43.3.10896-cb3ebc72d", latest="1.43.5.11000-aaaaaaaaa"),
    )
    assert code == 0
    assert "PLEX IS BEHIND ITS RELEASE TRAIN" not in text
    assert "PLEX IS ON ITS RELEASE TRAIN" in text
    assert "plex 1.43.3.10896-cb3ebc72d" in text


def test_plex_identity_unreadable_exits_one_rather_than_clearing(monkeypatch):
    """An unreachable Plex must never read like a Plex that is up to date."""
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
        plex=_plex(running=nas.Unreachable("plex answered 401 on /identity")),
    )
    assert code == 1
    assert "CANNOT JUDGE" in text
    assert "plex: /identity is unreadable" in text
    assert "plex could NOT be judged" in text
    assert "NO APP IS BEHIND ITS OWN RELEASE TRAIN" not in text


def test_plex_upstream_unreadable_exits_one_rather_than_clearing(monkeypatch):
    """Same contract the *arr half holds: no upstream, no verdict."""
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
        plex=_plex(latest=None, rows=0, why_not="URLError: timed out"),
    )
    assert code == 1
    assert "the newest published Plex build could not be read -- URLError" in text


def test_plex_version_that_cannot_be_compared_exits_one(monkeypatch):
    """A build string with no leading number is unjudged, never cleared."""
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
        plex=_plex(running="plexpass-nightly"),
    )
    assert code == 1
    assert "cannot be compared against plex.tv" in text


def test_plex_upstream_reads_only_the_synology_rows():
    """The manifest carries a row per platform; his box is a Synology."""
    manifest = {
        "computer": {"Linux": {"version": "9.9.9.1-linux"}},
        "nas": {
            "Synology (DSM 6)": {"version": "1.43.3.10896-cb3ebc72d"},
            "Synology (DSM 7)": {"version": "1.43.3.10896-cb3ebc72d"},
            "Synology (DSM 7.2.2+)": {"version": "1.43.3.10896-cb3ebc72d"},
            "QNAP": {"version": "2.0.0.1-qnap"},
        },
    }
    version, rows, why_not = nas_versions.plex_upstream(opener=_manifest_opener(manifest))
    assert (version, rows, why_not) == ("1.43.3.10896-cb3ebc72d", 3, None)


def test_plex_upstream_takes_the_newest_when_the_rows_disagree():
    """They agree today. If a DSM generation ever lags, the newest is upstream."""
    manifest = {"nas": {"Synology (DSM 6)": {"version": "1.41.6.9685-x"},
                        "Synology (DSM 7)": {"version": "1.43.3.10896-y"}}}
    version, rows, _ = nas_versions.plex_upstream(opener=_manifest_opener(manifest))
    assert (version, rows) == ("1.43.3.10896-y", 2)


def test_plex_upstream_with_no_synology_row_is_a_failure_not_an_empty_answer():
    manifest = {"nas": {"QNAP": {"version": "2.0.0.1-qnap"}}}
    version, rows, why_not = nas_versions.plex_upstream(opener=_manifest_opener(manifest))
    assert version is None and rows == 0
    assert "Synology" in why_not


def test_plex_upstream_that_is_not_json_is_a_failure():
    version, _, why_not = nas_versions.plex_upstream(opener=_raw_opener(b"<html>nope"))
    assert version is None and "not JSON" in why_not


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _raw_opener(body):
    def opener(url, timeout=None):
        return _FakeResponse(body)
    return opener


def _manifest_opener(manifest):
    import json as _json
    return _raw_opener(_json.dumps(manifest).encode())


# --- bazarr -----------------------------------------------------------------
#
# Added Cycle 661, and judged by its own `current` flag rather than by a
# version comparison -- the endpoint carrying its version does not answer.


def test_bazarr_not_on_any_listed_release_raises(monkeypatch):
    """The live shape as of Cycle 661: five stable rows and `current` on none."""
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
        bazarr=_bazarr(("v1.6.0", "2026-07-04", False, 5)),
    )
    assert code == 2
    assert "BAZARR IS BEHIND ITS OWN PUBLISHED RELEASES" in text
    assert "none of the 5 newest published release(s)" in text
    assert "the newest is v1.6.0 (2026-07-04)" in text


def test_bazarr_on_its_newest_release_is_printed_and_does_not_raise(monkeypatch):
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
    )
    assert code == 0
    assert "BAZARR IS ON ITS NEWEST PUBLISHED RELEASE" in text
    assert "BAZARR IS BEHIND" not in text


def test_bazarr_unreadable_exits_one_rather_than_clearing(monkeypatch):
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
        bazarr=_bazarr(nas.Unreachable("bazarr's front page carried no `apiKey`")),
    )
    assert code == 1
    assert "bazarr: its release standing is unreadable" in text
    assert "bazarr could NOT be judged" in text


def test_a_clean_sweep_names_bazarr_in_the_denominator(monkeypatch):
    _patch_config(monkeypatch, _conf("sonarr", "radarr"))
    code, text = _run(
        ("sonarr", "radarr"),
        {"sonarr": _status("4.0.19.2979"), "radarr": _status("6.3.0.10514")},
        {"Sonarr/Sonarr": "v4.0.19.2979", "Radarr/Radarr": "v6.3.0.10514"},
    )
    assert code == 0
    assert "plus plex plus bazarr" in text
    assert "5 app(s) run on that box" in text
