"""Tests for tools.claim_history.

The one that matters is `test_a_pruned_ledger_row_stays_in_the_history`: the
ledger forgets a released claim after a day, and the whole point of the
history is that it does not.
"""

import json
from datetime import datetime

from agora_runner import nova_claims
from tools import claim_history as ch


def _ledger(*rows):
    return json.dumps({"claims": list(rows)})


class FakeVault:
    def __init__(self, ledger, history=None, get_fails=(), put_error=None):
        self.docs = {ch.CLAIMS_PATH: ledger}
        if history is not None:
            self.docs[ch.HISTORY_PATH] = history
        self.get_fails = set(get_fails)
        self.put_error = put_error
        self.puts = []

    def get(self, path, rev_file=None):
        if path in self.get_fails:
            return "", "error"
        if path not in self.docs:
            return "", "absent"
        return self.docs[path], "ok"

    def put(self, path, local, rev_file):
        if self.put_error:
            return self.put_error
        self.docs[path] = open(local).read()
        self.puts.append(path)
        return None

    def rows(self):
        return json.loads(self.docs[ch.HISTORY_PATH])["rows"]


TAKE = {"item": "idea-312", "cycle": 1731, "state": "open",
        "at": "2026-09-17T04:38:00+02:00", "note": "slice 1"}
RELEASE = {**TAKE, "state": "done", "at": "2026-09-17T05:10:00+02:00",
           "outcome": "merged"}
SEQ = {"item": "journal-seq-1788", "cycle": 1731, "state": "done",
       "at": "2026-09-17T05:12:00+02:00", "outcome": "written"}


def test_first_run_creates_the_history_without_bookkeeping_rows(tmp_path):
    vault = FakeVault(_ledger(TAKE, SEQ))
    code, lines = ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    assert code == 0
    assert vault.rows() == [{**TAKE, "first_seen_at": TAKE["at"]}]
    assert "1 new and 0 updated" in lines[0]


def test_a_release_updates_the_row_and_keeps_when_it_was_taken(tmp_path):
    vault = FakeVault(_ledger(TAKE))
    ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    vault.docs[ch.CLAIMS_PATH] = _ledger(RELEASE)
    code, lines = ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    assert code == 0
    assert vault.rows() == [{**RELEASE, "first_seen_at": TAKE["at"]}]
    assert "0 new and 1 updated" in lines[0]


def test_a_pruned_ledger_row_stays_in_the_history(tmp_path):
    vault = FakeVault(_ledger(RELEASE))
    ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    # The real prune, a day and a bit later, empties the ledger.
    ledger = {"claims": [dict(RELEASE)]}
    nova_claims.prune(ledger, datetime.fromisoformat("2026-09-18T06:00:00+02:00"))
    assert ledger["claims"] == []
    # A later cycle's claim forces a write, so the history is rebuilt from
    # what it held plus the ledger -- and the pruned row has to survive that.
    later = {"item": "issue-227", "cycle": 1760, "state": "open",
             "at": "2026-09-18T06:00:00+02:00", "note": "next"}
    ledger["claims"].append(later)
    vault.docs[ch.CLAIMS_PATH] = json.dumps(ledger)
    code, lines = ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    assert code == 0
    assert vault.puts == [ch.HISTORY_PATH, ch.HISTORY_PATH]
    assert [r["item"] for r in vault.rows()] == ["idea-312", "issue-227"]


def test_nothing_new_does_not_write(tmp_path):
    vault = FakeVault(_ledger(TAKE))
    ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    assert vault.puts == [ch.HISTORY_PATH]


def test_the_same_item_in_two_cycles_is_two_rows(tmp_path):
    resumed = {**TAKE, "cycle": 1732, "resumed_from": 1731}
    vault = FakeVault(_ledger(RELEASE, resumed))
    ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    assert [(r["item"], r["cycle"]) for r in vault.rows()] == [
        ("idea-312", 1731), ("idea-312", 1732)]


def test_an_unreadable_ledger_is_not_clean(tmp_path):
    vault = FakeVault(_ledger(TAKE), get_fails={ch.CLAIMS_PATH})
    code, _ = ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    assert code == 1
    assert vault.puts == []


def test_an_unreadable_history_is_never_overwritten(tmp_path):
    vault = FakeVault(_ledger(TAKE), history="[]", get_fails={ch.HISTORY_PATH})
    code, _ = ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    assert code == 1
    assert vault.puts == []


def test_a_malformed_history_is_never_overwritten(tmp_path):
    vault = FakeVault(_ledger(TAKE), history="not json")
    code, _ = ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    assert code == 1
    assert vault.docs[ch.HISTORY_PATH] == "not json"


def test_a_lost_write_is_not_clean(tmp_path):
    vault = FakeVault(_ledger(TAKE), put_error="409 conflict")
    code, lines = ch.report(get=vault.get, put=vault.put, workdir=tmp_path)
    assert code == 1
    assert "409 conflict" in lines[0]
