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
    assert nds.classify(entries, min_runs=4) == ([], [])
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


def test_render_names_the_category_and_the_last_run():
    dead, quiet = nds.classify(
        _stats({"Hacking": {"rss": {"guardian.co.uk": {"runs": 82, "written": 0, "fetch_errors": 82,
                                                       "last_run_at": "2026-08-25T00:36:14Z"}}}})
    )
    out = nds.render(dead, quiet, 4)
    assert "Hacking" in out
    assert "2026-08-25T00:36:14Z" in out
    assert "82 errors" in out.replace("  ", " ")
