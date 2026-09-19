"""`tools.eol_watch --publish`'s node record (idea #322)."""
import datetime as dt

from tools import eol_watch

TODAY = dt.date(2026, 9, 19)


def _pin(node, days):
    return {"path": f"node {node}", "tag": "1.34.4", "kubelet": "v1.34.4+k3s1",
            "kind": "node", "days": days}


def test_a_node_inside_the_window_sets_warn_and_carries_its_date():
    record = eol_watch.node_record([_pin("server1", 38), _pin("server2", 38)], [], TODAY)
    assert record["warn"] is True
    assert record["nodes"][0] == {"node": "server1", "kubelet": "v1.34.4+k3s1",
                                  "securityEnds": "2026-10-27", "days": 38}


def test_the_window_edge_is_inclusive_and_one_day_past_it_is_quiet():
    edge = eol_watch.NODE_WARN_DAYS
    assert eol_watch.node_record([_pin("server1", edge)], [], TODAY)["warn"] is True
    assert eol_watch.node_record([_pin("server1", edge + 1)], [], TODAY)["warn"] is False


def test_a_node_problem_is_published_as_an_error_not_as_quiet():
    record = eol_watch.node_record([], ["could not read nodes: boom"], TODAY)
    assert record["error"] == "could not read nodes: boom"


def test_publish_reports_a_refused_write():
    class Done:
        returncode, stderr, stdout = 1, "refused", ""
    assert eol_watch.publish({"nodes": []}, runner=lambda *a, **k: Done()) == "refused"
