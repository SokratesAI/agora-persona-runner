"""Tests for tools.host_cpu_ingest.

The two that matter are `test_unrecorded_sweeps_that_cannot_be_read_are_not_clean`
and `test_a_ledger_already_level_with_the_cluster_is_clean`. They are the two
sides of the same exit code: a run that ingested nothing because there was
nothing to ingest, and a run that ingested nothing because it could not read
what was there. Those look identical from outside and mean opposite things,
which is the failure `tools.host_cpu_history` exists to stop one layer down.
"""

import json

import pytest

from tests.test_host_memory_trend import (SWEEP_LOG_WITH_CPU, FakeProc,
                                          _backfill_pods, sweep_runner)
from tools import host_cpu_ingest as ingest


@pytest.fixture(autouse=True)
def _never_touch_the_real_cpu_ledger(tmp_path_factory, monkeypatch):
    """No test in this file writes the production ledger at /data.

    The same guard `tests/test_host_memory_trend.py` grew after a `-k` run
    appended 22 live samples to it: `main()` here defaults `--cpu-ledger` to
    the production path, so a test that forgets the flag writes real rows.
    """
    monkeypatch.setattr(ingest, "DEFAULT_CPU_LEDGER",
                        str(tmp_path_factory.mktemp("ledger") / "cpu.jsonl"))


def test_every_retained_sweep_the_ledger_never_saw_is_ingested(tmp_path):
    ledger = tmp_path / "cpu.jsonl"
    run = sweep_runner(pods=_backfill_pods(), log=SWEEP_LOG_WITH_CPU,
                       nodes=("server1", "server2"))
    code, lines = ingest.report(str(ledger), runner=run)
    assert code == 0
    assert "ingested 6 CPU sample(s) from 3 retained sweep(s)" in lines[0]
    stamps = sorted({json.loads(line)["_at"]
                     for line in ledger.read_text().splitlines()})
    assert stamps == ["2026-08-31T11:30:00+00:00", "2026-08-31T12:30:00+00:00",
                      "2026-08-31T13:30:00+00:00"]


def test_the_count_it_reports_is_read_back_rather_than_added(tmp_path):
    # `record_cpu` trims to `keep` on the way in, so a ledger that was full
    # before the run holds fewer sweeps afterwards than before + ingested.
    ledger = tmp_path / "cpu.jsonl"
    run = sweep_runner(pods=_backfill_pods(), log=SWEEP_LOG_WITH_CPU,
                       nodes=("server1", "server2"))
    code, lines = ingest.report(str(ledger), keep=2, runner=run)
    assert code == 0
    assert "now holds 2 sweep Pod(s)" in lines[0]
    assert len(ledger.read_text().splitlines()) == 2


def test_a_ledger_already_level_with_the_cluster_is_clean(tmp_path):
    ledger = tmp_path / "cpu.jsonl"
    ingest.report(str(ledger), runner=sweep_runner(
        pods=_backfill_pods(), log=SWEEP_LOG_WITH_CPU, nodes=("server1", "server2")))
    again = sweep_runner(pods=_backfill_pods(), log=SWEEP_LOG_WITH_CPU,
                         nodes=("server1", "server2"))
    code, lines = ingest.report(str(ledger), runner=again)
    assert code == 0
    assert lines[0].startswith("current — every retained sweep is already in")
    assert "holds 6 sweep Pod(s)" in lines[0]
    # The steady state is one `kubectl get pods` and no log read at all; that
    # is what lets this run every cycle instead of every six hours.
    assert [argv for argv in again.seen if argv[1] == "logs"] == []


def test_unrecorded_sweeps_that_cannot_be_read_are_not_clean(tmp_path):
    ledger = tmp_path / "cpu.jsonl"
    run = sweep_runner(pods=_backfill_pods(), log=SWEEP_LOG_WITH_CPU,
                       logs_rc=1, nodes=("server1", "server2"))
    code, lines = ingest.report(str(ledger), runner=run)
    assert code == 1
    assert "INGESTED NOTHING — 3 retained sweep(s)" in lines[0]
    assert not ledger.exists()


def test_a_kubectl_failure_is_reported_rather_than_read_as_current(tmp_path):
    def run(argv, **kwargs):
        return FakeProc(stdout="", returncode=1, stderr="forbidden")

    code, lines = ingest.report(str(tmp_path / "cpu.jsonl"), runner=run)
    assert code == 1
    assert "COULD NOT INGEST" in lines[0]
    assert "forbidden" in lines[0]


def test_an_unreadable_ledger_is_not_silently_rewritten(tmp_path, monkeypatch):
    ledger = tmp_path / "cpu.jsonl"
    ledger.write_text("{}\n")
    monkeypatch.setattr(ingest, "ledger_pods",
                        lambda path: (None, "permission denied"))
    called = []
    monkeypatch.setattr(ingest, "backfill_cpu",
                        lambda *a, **k: called.append(a) or (0, 0, None))
    code, lines = ingest.report(str(ledger))
    assert code == 1
    assert "COULD NOT READ the CPU ledger" in lines[0]
    # It must not go on to write a ledger it could not read: the skip set
    # would be empty and every retained sweep would be appended again.
    assert called == []


def test_main_prints_the_report_and_returns_its_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ingest, "report", lambda *a, **k: (1, ["COULD NOT INGEST — nope"]))
    code = ingest.main(["--cpu-ledger", str(tmp_path / "cpu.jsonl")])
    out = capsys.readouterr().out
    assert code == 1
    assert "HOST CPU INGEST" in out
    assert "COULD NOT INGEST — nope" in out


def test_main_defaults_to_the_durable_ledger_the_reader_reads(monkeypatch):
    # The one wiring with no other test: a default pointing anywhere else would
    # let this check pass forever while `tools.host_cpu_history` stayed blind,
    # which is exactly the shape of the bug it was built for. The autouse
    # fixture above redirects the constant, so this puts the real one back for
    # the length of the call rather than asserting a patched value against
    # itself.
    from tools import host_memory_trend as hmt
    monkeypatch.setattr(ingest, "DEFAULT_CPU_LEDGER", hmt.DEFAULT_CPU_LEDGER)
    seen = {}

    def fake_report(path, **kwargs):
        seen["path"] = path
        return 0, ["ok"]

    monkeypatch.setattr(ingest, "report", fake_report)
    ingest.main([])
    assert seen["path"] == "/data/nova-host-cpu.jsonl"
