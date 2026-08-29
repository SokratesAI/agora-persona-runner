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


def _run(services, statuses, upstream, ssh=HOP):
    out = io.StringIO()
    code = nas_versions.report(
        out=out,
        ssh=ssh,
        get=_get_returning(statuses),
        latest_release=_upstream(upstream),
        now=NOW,
        env={},
        run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no shell in these tests")),
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
    assert "Judged the running version of 2 service(s) of 2" in text


def test_a_minor_gap_is_printed_and_does_not_raise(monkeypatch):
    """A minor release must not turn this red -- that is the whole threshold.

    Without this the check fires on projects that ship continuously, and a
    check that is always red is the same as no check. `4.0.x` against `4.2.x`
    is the case that pins the threshold at *major*: my first version of this
    test used `4.0.14` against `4.0.19`, which is a patch gap, so widening the
    rule to `("major", "minor")` left the whole suite green. A threshold test
    has to sit on the boundary it is claiming.
    """
    _patch_config(monkeypatch, _conf("sonarr"))
    code, text = _run(
        ("sonarr",),
        {"sonarr": _status("4.0.14.2939")},
        {"Sonarr/Sonarr": "v4.2.0.2979"},
    )
    assert code == 0
    assert "MAJOR VERSION BEHIND" not in text
    assert "NOT A MAJOR BEHIND" in text
    assert "sonarr 4.0.14.2939" in text
    assert "NO APP IS A MAJOR BEHIND ITS OWN PROJECT on 1 service(s)" in text


def test_a_patch_gap_is_printed_and_does_not_raise(monkeypatch):
    """The other side of the same threshold, one step in."""
    _patch_config(monkeypatch, _conf("radarr"))
    code, text = _run(
        ("radarr",),
        {"radarr": _status("6.3.0.10500")},
        {"Radarr/Radarr": "v6.3.1.10514"},
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
    _patch_config(monkeypatch, _conf("sonarr"))
    code, text = _run(("sonarr",), {"sonarr": _status("nightly")},
                      {"Sonarr/Sonarr": "v4.0.19.2979"})
    assert code == 1
    assert "CANNOT JUDGE" in text
    assert "cannot be compared" in text
    assert "NOT A MAJOR BEHIND" not in text
    # It is not counted as judged either -- a partial sweep must say so.
    assert "Judged the running version of 0 service(s) of 1" in text


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


def test_upstream_table_covers_every_service_nas_knows_about():
    """A new service in `tools.nas` must not silently fall out of this sweep.

    Without this, adding one lands it in `CANNOT JUDGE` forever with a message
    about a mapping, which reads like a NAS problem and is a code problem.
    """
    assert set(nas.SERVICES) == set(nas_versions.UPSTREAM)
