"""Tests for tools.memory_headroom.

The failing case is built first and deliberately, because the healthy
verdict is the one this tool returns on both real pods today: a check
whose only exercised path is `exit 0` and a check that cannot fail look
the same from outside.

The rest pin the mistake the module exists to prevent. `memory.peak` sat
at 99.6% of the limit on the bridge pod while `memory.events.max` was 0,
so a peak-based judgement raises on a healthy container and a
counter-based one does not. `test_a_peak_at_the_limit_is_not_a_finding`
is that reading, verbatim, asserted to exit 0 — if anyone ever wires the
peak into `judge`, it fails.
"""

import pytest

from tools import memory_headroom


#: The bridge pod, read 2026-08-31 12:37 Oslo. Peak 7 MiB below a hard kill
#: boundary, `max 0`, and healthy.
BRIDGE = {
    "memory.max": "2147483648",
    "memory.current": "1225273344",
    "memory.peak": "2139783168",
    "memory.events": "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n",
    "memory.stat": "anon 266137600\nfile 921509888\nkernel 40976384\n"
                   "slab 38437240\ninactive_file 727896064\nactive_file 193613824\n",
}

#: The runner pod, same cycle. Same verdict, peak at 19% of its limit — which
#: is why the peak carries no signal either way.
RUNNER = {
    "memory.max": "268435456",
    "memory.current": "42352640",
    "memory.peak": "50835456",
    "memory.events": "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n",
    "memory.stat": "anon 39493632\nkernel 1265664\nslab 792784\ninactive_file 921600\n",
}


def cgroup(tmp_path, files):
    root = tmp_path / "cgroup"
    root.mkdir(exist_ok=True)
    for name, body in files.items():
        (root / name).write_text(body)
    return str(root)


def verdict(tmp_path, files):
    reading, why = memory_headroom.read_cgroup(cgroup(tmp_path, files))
    assert why is None, why
    return memory_headroom.judge(reading)


def test_reclaim_at_the_limit_is_the_finding(tmp_path):
    files = dict(BRIDGE)
    files["memory.events"] = BRIDGE["memory.events"].replace("max 0", "max 7")
    lines, code = verdict(tmp_path, files)
    assert code == 2
    assert any("SQUEEZED AT THE LIMIT" in line and "memory.events.max is 7" in line
               for line in lines)


def test_an_oom_kill_is_the_finding(tmp_path):
    files = dict(BRIDGE)
    files["memory.events"] = BRIDGE["memory.events"].replace("oom_kill 0", "oom_kill 1")
    lines, code = verdict(tmp_path, files)
    assert code == 2
    assert any("memory.events.oom_kill is 1" in line for line in lines)


@pytest.mark.parametrize("files,name", [(BRIDGE, "bridge"), (RUNNER, "runner")])
def test_a_peak_at_the_limit_is_not_a_finding(tmp_path, files, name):
    """Both real pods, both healthy, peaks at 99.6% and 19% of their limits."""
    lines, code = verdict(tmp_path, files)
    assert code == 0, f"{name}: judged harm with every events counter at 0"
    assert any("NOT JUDGED" in line and "memory.peak" in line for line in lines)


def test_the_page_cache_is_kept_out_of_the_level(tmp_path):
    """anon+kernel, not memory.current — the cache is reclaimed, not fatal."""
    reading, _ = memory_headroom.read_cgroup(cgroup(tmp_path, BRIDGE))
    assert memory_headroom.unreclaimable(reading) == 266137600 + 40976384
    assert memory_headroom.working_set(reading) == 1225273344 - 727896064
    lines, _ = memory_headroom.judge(reading)
    assert "anonymous+kernel 292.9 MiB (14.3% of the limit)" in lines[0]


def test_a_missing_events_file_is_unreadable_not_healthy(tmp_path):
    files = {k: v for k, v in BRIDGE.items() if k != "memory.events"}
    reading, why = memory_headroom.read_cgroup(cgroup(tmp_path, files))
    assert reading is None
    assert "squeezed at its limit" in why
    assert memory_headroom.main(["--cgroup-root", cgroup(tmp_path, files)]) == 1


def test_a_counterless_events_file_is_unreadable_not_healthy(tmp_path):
    """cgroup v2 exists but the counters this judges on do not."""
    files = dict(BRIDGE)
    files["memory.events"] = "low 0\nhigh 0\n"
    reading, why = memory_headroom.read_cgroup(cgroup(tmp_path, files))
    assert reading is None
    assert "max" in why and "oom_kill" in why


def test_an_unlimited_cgroup_still_judges_the_counters(tmp_path):
    files = dict(BRIDGE)
    files["memory.max"] = "max"
    files["memory.events"] = BRIDGE["memory.events"].replace("oom_kill 0", "oom_kill 2")
    reading, why = memory_headroom.read_cgroup(cgroup(tmp_path, files))
    assert why is None
    assert reading["limit"] is None
    lines, code = memory_headroom.judge(reading)
    assert code == 2
    assert any("no limit set" in line for line in lines)


def test_main_exits_zero_on_a_healthy_cgroup(tmp_path, capsys):
    """The closing line carries a count, which is what preflight summarises on.

    `tools.preflight.summary_line` takes the last line carrying a digit and
    falls back to the last non-empty one. Without a count here it fell back to
    the CANNOT JUDGE line, so the table said what this could not see rather
    than what it found.
    """
    assert memory_headroom.main(["--cgroup-root", cgroup(tmp_path, RUNNER)]) == 0
    closing = capsys.readouterr().out.strip().splitlines()[-1]
    assert "Judged 1 cgroup" in closing
    assert "0 of them is non-zero" in closing
    assert any(char.isdigit() for char in closing)


def test_the_closing_count_moves_with_the_verdict(tmp_path, capsys):
    files = dict(BRIDGE)
    files["memory.events"] = BRIDGE["memory.events"].replace("oom_kill 0", "oom_kill 4")
    assert memory_headroom.main(["--cgroup-root", cgroup(tmp_path, files)]) == 2
    assert "1 of them is non-zero" in capsys.readouterr().out
