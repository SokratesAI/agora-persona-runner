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


# --- what the histogram cannot see (Cycle 935) -------------------------
#
# The second reading of this route, 74 minutes into the same pod, said
# tracked objects fell 41,372 -> 32,140 while RSS rose 102.4 -> 150.1 MiB.
# A population histogram over `gc.get_objects()` can only ever see
# containers, so a process retaining nothing but large buffers reads as a
# shrinking heap there. These tests are about the two sections added to
# close that: the buffers, and the allocator underneath them.


def test_untracked_footprint_sees_a_buffer_the_histogram_cannot():
    big = "x" * 100_000
    objects = [{"body": big}]

    # The precondition, asserted rather than assumed: the histogram is
    # blind to this string. Without this line the test below would pass
    # just as happily against a histogram that had started counting
    # strings, and would be measuring nothing.
    assert "str" not in {row["type"] for row in nova_site.type_histogram(objects)}

    out = nova_site.untracked_footprint(objects)
    by_type = {row["type"]: row for row in out["types"]}
    assert "str" in by_type
    assert by_type["str"]["bytes"] >= 100_000
    assert out["largest_bytes"] >= 100_000
    assert out["bytes_total"] >= 100_000


def test_untracked_footprint_counts_a_shared_buffer_once():
    """Two holders of one payload is 5 MB retained, not 10."""
    big = b"y" * 50_000
    objects = [{"a": big}, {"b": big}]
    out = nova_site.untracked_footprint(objects)
    by_type = {row["type"]: row for row in out["types"]}
    assert by_type["bytes"]["count"] == 1
    assert by_type["bytes"]["bytes"] < 100_000


def test_untracked_footprint_ignores_the_containers_themselves():
    """`bytes_total` is the leaves; the histogram already has the rest."""
    objects = [[1, 2, 3], {"n": "leaf"}]
    out = nova_site.untracked_footprint(objects)
    assert out["distinct"] == 1          # the value; ints are not leaves here
    assert [row["type"] for row in out["types"]] == ["str"]


def test_untracked_footprint_cannot_see_a_string_dict_key():
    """A limit found by writing the test wrong, kept so nobody finds it twice.

    CPython's dict traversal skips the keys of a unicode-keyed dict -- a
    string references nothing, so it can never be part of a cycle and the
    collector has no reason to walk it. `gc.get_referents` inherits that,
    so this function sees dict *values* and not dict *keys*. Keys are
    short and interned and that is why the omission is affordable, but it
    is an omission and not a rounding error.
    """
    objects = [{"a-key-of-some-length": 1}]
    assert nova_site.untracked_footprint(objects)["distinct"] == 0


def test_malloc_trim_reports_what_the_kernel_said_either_side(monkeypatch):
    readings = iter([{"rss_bytes": 200 * 1024 * 1024},
                     {"rss_bytes": 150 * 1024 * 1024}])
    monkeypatch.setattr(nova_site, "read_proc_status", lambda path: next(readings))

    class _Trim:
        argtypes = None
        restype = None

        def __call__(self, _pad):
            return 1

    class _Libc:
        malloc_trim = _Trim()

    monkeypatch.setattr(nova_site.ctypes, "CDLL", lambda *a, **k: _Libc())

    out = nova_site.malloc_trim("/proc/self/status")
    assert out["available"] is True
    assert out["released"] is True
    assert out["freed_bytes"] == 50 * 1024 * 1024


def test_malloc_trim_without_glibc_says_so_rather_than_zero(monkeypatch):
    """No allocator to ask must not read as an allocator holding nothing."""
    def _no_libc(*a, **k):
        raise OSError("libc.so.6: cannot open shared object file")

    monkeypatch.setattr(nova_site.ctypes, "CDLL", _no_libc)
    out = nova_site.malloc_trim("/proc/self/status")
    assert out["available"] is False
    assert "libc.so.6" in out["reason"]
    assert "freed_bytes" not in out


def test_report_carries_untracked_always_and_trim_only_when_asked():
    objects = [{"body": "z" * 10_000}]
    plain = nova_site.memory_report(objects=objects)
    assert plain["untracked"]["bytes_total"] >= 10_000
    assert "malloc_trim" not in plain

    asked = nova_site.memory_report(objects=objects, trim=True)
    assert "malloc_trim" in asked


def test_a_cache_rebuild_trims_the_arena(monkeypatch):
    """The reading issue #131 was waiting for, turned into a guard.

    Measured on the live pod 2026-09-05: ten journal rebuilds added 8.09
    MiB of RSS while `untracked` did not move one byte, and a single
    `malloc_trim(0)` gave back 26.18 MiB. So the growth is pages glibc is
    holding, and `_refresh` is where they are freed -- assert that a
    rebuild actually asks for them back, rather than that the helper
    exists.
    """
    calls = []
    monkeypatch.setattr(nova_site, "_trim_after_rebuild", lambda: calls.append(1))
    nova_site._cache.pop("t-trim", None)
    nova_site._refresh("t-trim", lambda: {"n": 1})
    assert calls == [1], "a rebuild must trim; without it RSS only ever goes up"


def test_the_trim_survives_an_image_with_no_glibc(monkeypatch):
    """A musl image must not take a cache refresh down over an optimisation."""
    def no_libc(*a, **k):
        raise OSError("libc.so.6: cannot open shared object file")
    monkeypatch.setattr(nova_site.ctypes, "CDLL", no_libc)
    nova_site._trim_after_rebuild()
