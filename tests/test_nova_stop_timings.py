"""The stop-timing ledger behind nova-kr-control-stop-seconds (issue #240)."""
from datetime import datetime, timezone

import pytest

from agora_runner import nova_stop_timings as st


def test_record_appends_to_what_it_read_and_writes_on_that_revision():
    existing = st.dumps([{"at": "2026-09-17T01:00:00+00:00", "seconds": 2.0}])
    writes = []
    st.record(3.456, read=lambda p: (existing, "rev-7"),
              write=lambda p, body, if_rev: writes.append((p, body, if_rev)),
              now=datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc))
    path, body, rev = writes[0]
    assert path == st.STOP_TIMINGS_PATH and rev == "rev-7"
    assert st.load(body) == [
        {"at": "2026-09-17T01:00:00+00:00", "seconds": 2.0},
        {"at": "2026-09-17T02:00:00+00:00", "seconds": 3.46}]


def test_record_starts_a_ledger_that_does_not_exist_yet():
    writes = []
    st.record(1.0, read=lambda p: (None, None),
              write=lambda p, body, if_rev: writes.append(body))
    assert len(st.load(writes[0])) == 1


def test_a_lost_race_rereads_instead_of_overwriting():
    reads = iter([(st.dumps([]), "r1"),
                  (st.dumps([{"at": "a", "seconds": 9.0}]), "r2")])
    writes = []

    def write(path, body, if_rev):
        if if_rev == "r1":
            raise RuntimeError("conflict")
        writes.append(body)

    st.record(1.0, read=lambda p: next(reads), write=write)
    assert [r["seconds"] for r in st.load(writes[0])] == [9.0, 1.0]


def test_a_ledger_of_the_wrong_shape_refuses_rather_than_reading_empty():
    with pytest.raises(ValueError):
        st.load('{"something": []}')
