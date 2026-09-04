"""`/api/health/memory` -- the instrument for issue #131.

nova-site's RSS climbs with traffic and never comes back down. The limit
has been raised twice for it and neither raise named what is retained,
because the process exposes no view of its own heap. These tests are
about that report being trustworthy enough to compare two readings of:
an absent measurement must not read as a zero one, and the histogram has
to count what it was handed rather than whatever the test runner holds.
"""

import gc
import tracemalloc

import pytest

from agora_runner import nova_site


def test_proc_status_reads_bytes_not_kilobytes(tmp_path):
    status = tmp_path / "status"
    status.write_text(
        "Name:\tpython3\n"
        "VmSize:\t  123456 kB\n"
        "VmRSS:\t    2048 kB\n"
        "VmHWM:\t    4096 kB\n"
        "RssAnon:\t 1024 kB\n"
        "Threads:\t9\n"
    )
    out = nova_site.read_proc_status(str(status))
    assert out == {
        "vmsize_bytes": 123456 * 1024,
        "rss_bytes": 2048 * 1024,
        "rss_peak_bytes": 4096 * 1024,
        "rss_anon_bytes": 1024 * 1024,
    }


def test_unreadable_status_is_empty_not_zero(tmp_path):
    """The difference between "holds nothing" and "I could not ask"."""
    out = nova_site.read_proc_status(str(tmp_path / "does-not-exist"))
    assert out == {}
    assert "rss_bytes" not in out


def test_histogram_counts_what_it_was_given():
    objects = [1, 2, 3, "a", "b", {}, [], []]
    hist = nova_site.type_histogram(objects, top=10)
    counts = {row["type"]: row["count"] for row in hist}
    assert counts["int"] == 3
    assert counts["str"] == 2
    assert counts["list"] == 2
    assert counts["dict"] == 1
    # Most numerous first, so a climbing population is the top row.
    assert [row["count"] for row in hist] == sorted(
        (row["count"] for row in hist), reverse=True
    )


def test_histogram_top_is_a_cut_not_a_sample():
    objects = [1] * 5 + ["a"] * 4 + [()] * 3
    hist = nova_site.type_histogram(objects, top=2)
    assert [row["type"] for row in hist] == ["int", "str"]


def test_cache_footprint_measures_the_bodies_it_holds():
    nova_site.reset_cache()
    try:
        nova_site.cached_payload("journal", lambda: {"rows": ["x" * 500]})
        foot = nova_site.cache_footprint()
        assert foot["entries"] == 1
        assert foot["keys"][0]["key"] == "journal"
        # The served body, not the payload dict: it is the string this
        # module actually keeps.
        assert foot["keys"][0]["body_bytes"] > 500
        assert foot["body_bytes_total"] == foot["keys"][0]["body_bytes"]
    finally:
        nova_site.reset_cache()


def test_report_says_tracing_is_off_rather_than_returning_nothing():
    """An empty `top` with no flag beside it reads as "nothing allocated"."""
    if tracemalloc.is_tracing():
        pytest.skip("tracing already on in this interpreter")
    report = nova_site.memory_report(objects=[1, "a"])
    assert report["tracemalloc"]["tracing"] is False
    assert report["tracemalloc"]["top"] == []


def test_report_carries_the_four_things_a_second_reading_is_compared_on():
    report = nova_site.memory_report(objects=[1, "a", {}])
    assert set(report) >= {"process", "gc", "threads", "cache", "types", "tracemalloc"}
    assert report["gc"]["tracked_objects"] == 3
    assert isinstance(report["gc"]["counts"], list)
    assert isinstance(report["threads"], list)


def test_report_is_json_serialisable():
    """It is sent as JSON; a value that is not is a 500 on the one route
    that gets asked when the pod is already in trouble."""
    import json

    json.dumps(nova_site.memory_report(objects=gc.get_objects()[:200]))


def test_route_serves_a_report_and_not_just_a_200():
    """A 200 on its own proves nothing -- a shell, a proxy or the wrong
    handler answers the same. Assert on the one thing only this route can
    produce: a type histogram of this very process's heap.
    """
    from tests.test_nova_site import _get
    import json

    status, head, body = _get("/api/health/memory")
    assert status == 200, head
    report = json.loads(body)
    assert report["gc"]["tracked_objects"] > 1000
    names = {row["type"] for row in report["types"]}
    assert {"dict", "function"} <= names
    assert report["cache"]["entries"] >= 0
