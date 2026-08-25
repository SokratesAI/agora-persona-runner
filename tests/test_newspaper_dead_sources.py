import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import newspaper_dead_sources as nds


def _stats(entries):
    """entries: {category: {mode: {host: {runs, written, fetch_errors}}}}"""
    return {"source_stats": {"categories": entries}}


def test_every_run_errored_and_nothing_written_is_dead():
    dead, quiet = nds.classify(
        _stats({"Hacking": {"rss": {"guardian.co.uk": {"runs": 82, "written": 0, "fetch_errors": 82}}}})
    )
    assert [r["host"] for r in dead] == ["guardian.co.uk"]
    assert quiet == []


def test_a_source_that_fetches_and_writes_nothing_is_quiet_not_dead():
    dead, quiet = nds.classify(
        _stats({"Garden": {"scrape": {"example.com": {"runs": 20, "written": 0, "fetch_errors": 0}}}})
    )
    assert dead == []
    assert [r["host"] for r in quiet] == ["example.com"]


def test_a_source_that_errored_on_most_runs_but_not_all_is_quiet():
    # espn.com on the live paper: 11 errors in 12 runs, nothing written. It
    # fetched once, so the fetch is not what is stopping it and a config
    # change would not help. Dead means *every* run errored.
    dead, quiet = nds.classify(
        _stats({"Sport": {"scrape": {"espn.com": {"runs": 12, "written": 0, "fetch_errors": 11}}}})
    )
    assert dead == []
    assert [r["host"] for r in quiet] == ["espn.com"]


def test_a_source_that_has_written_is_neither():
    dead, quiet = nds.classify(
        _stats({"Sport": {"scrape": {"skysports.com": {"runs": 12, "written": 28, "fetch_errors": 3}}}})
    )
    assert dead == []
    assert quiet == []


def test_a_new_source_below_the_run_floor_is_not_called_dead():
    entries = _stats({"Sport": {"scrape": {"new.example": {"runs": 2, "written": 0, "fetch_errors": 2}}}})
    dead, quiet = nds.classify(entries, min_runs=4)
    assert dead == []
    assert [r["host"] for r in quiet] == ["new.example"]  # listed, just not condemned
    dead, _ = nds.classify(entries, min_runs=2)
    assert [r["host"] for r in dead] == ["new.example"]


def test_dead_sources_sort_worst_first():
    dead, _ = nds.classify(
        _stats(
            {
                "A": {"rss": {"few.example": {"runs": 5, "written": 0, "fetch_errors": 5}}},
                "B": {"rss": {"many.example": {"runs": 82, "written": 0, "fetch_errors": 82}}},
            }
        )
    )
    assert [r["host"] for r in dead] == ["many.example", "few.example"]


def test_exit_2_when_a_source_is_dead_and_0_when_none_are(tmp_path, monkeypatch, capsys):
    payload = _stats({"Hacking": {"rss": {"guardian.co.uk": {"runs": 82, "written": 0, "fetch_errors": 82}}}})
    monkeypatch.setattr(nds, "fetch_stats", lambda url, **kw: payload)
    assert nds.main(["--url", "http://stub"]) == 2
    assert "DEAD SOURCES" in capsys.readouterr().out

    monkeypatch.setattr(nds, "fetch_stats", lambda url, **kw: _stats({}))
    assert nds.main(["--url", "http://stub"]) == 0
    assert "No dead sources" in capsys.readouterr().out


def test_an_unreadable_endpoint_is_exit_1_not_a_clean_sweep(monkeypatch, capsys):
    def boom(url, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(nds, "fetch_stats", boom)
    assert nds.main(["--url", "http://stub"]) == 1
    err = capsys.readouterr().err
    assert "COULD NOT READ" in err
    assert "no instrument" in err


def test_the_run_floor_hides_a_dead_verdict_but_never_the_source():
    # A source with no errors is unambiguous however few runs it has. The
    # floor exists so one bad night cannot condemn a new source; dropping a
    # clean one from both lists would hide it instead of judging it.
    dead, quiet = nds.classify(
        _stats({"Sport": {"scrape": {"brand.new": {"runs": 1, "written": 0, "fetch_errors": 0}}}}),
        min_runs=4,
    )
    assert dead == []
    assert [r["host"] for r in quiet] == ["brand.new"]


def test_a_source_that_has_never_run_is_not_dead_even_with_the_floor_off():
    dead, quiet = nds.classify(
        _stats({"Sport": {"scrape": {"never.ran": {"runs": 0, "written": 0, "fetch_errors": 0}}}}),
        min_runs=0,
    )
    assert dead == []
    assert [r["host"] for r in quiet] == ["never.ran"]


def test_a_last_fetch_that_worked_rules_out_dead_however_the_counters_read():
    dead, quiet = nds.classify(
        _stats(
            {
                "Sport": {
                    "scrape": {
                        "flaky.example": {
                            "runs": 12,
                            "written": 0,
                            "fetch_errors": 14,
                            "last_fetch_ok": True,
                        }
                    }
                }
            }
        )
    )
    assert dead == []
    assert [r["host"] for r in quiet] == ["flaky.example"]


def test_a_malformed_payload_is_skipped_not_a_traceback():
    stats = {"source_stats": {"categories": {"A": None, "B": {"rss": None}, "C": {"rss": {"h": None}}}}}
    assert nds.classify(stats) == ([], [])


def test_a_payload_that_arrived_but_is_shaped_wrong_is_exit_1(monkeypatch, capsys):
    monkeypatch.setattr(nds, "fetch_stats", lambda url, **kw: {"source_stats": {"categories": "not a dict"}})
    assert nds.main(["--url", "http://stub"]) == 1
    assert "COULD NOT READ" in capsys.readouterr().err


def test_fetch_stats_actually_parses_what_the_endpoint_returns(monkeypatch):
    captured = {}

    class _Response:
        def read(self):
            return json.dumps({"source_stats": {"categories": {}}}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(nds.urllib.request, "urlopen", fake_urlopen)
    assert nds.fetch_stats("http://stub/api/source-stats") == {"source_stats": {"categories": {}}}
    assert captured["url"] == "http://stub/api/source-stats"
    assert captured["timeout"] == 20.0


def test_render_names_the_category_and_the_last_run():
    dead, quiet = nds.classify(
        _stats({"Hacking": {"rss": {"guardian.co.uk": {"runs": 82, "written": 0, "fetch_errors": 82,
                                                       "last_run_at": "2026-08-25T00:36:14Z"}}}})
    )
    out = nds.render(dead, quiet, 4)
    assert "Hacking" in out
    assert "2026-08-25T00:36:14Z" in out
    assert "82 errors" in out.replace("  ", " ")
