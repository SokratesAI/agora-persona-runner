"""`tools.demo reap` moves a stopped demo's leftover directory into restore/.

Key result `demos-kr-no-litter`: `stop` and `reap` drop a demo's row and
keep its files, and only `discard` removed them, which nothing ran. These
go at the three ways a sweep that moves directories can hurt: moving a
running demo's files, moving a demo a cycle is still writing, and moving
everything because the registry read came back empty.
"""

import os
import time
from unittest.mock import patch

from agora_runner import nova_demos
from tools import demo as demo_tool

HOUR = 3600


def _tree(tmp_path, names, age):
    root = tmp_path / "workspace" / "demos"
    for name in names:
        (root / name).mkdir(parents=True)
        page = root / name / "index.html"
        page.write_text("<h1>hi</h1>")
        old = time.time() - age
        os.utime(page, (old, old))
        os.utime(root / name, (old, old))
    return root


def _running(root, slug):
    return {"slug": slug, "host": "10.42.0.1", "port": 5174,
            "dir": str(root / slug), "pid": 1, "started_at": "2026-09-16T10:00:00"}


def _sweep(root, registry, now=None):
    with patch.object(nova_demos, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "DURABLE_ROOT", str(root)):
        return demo_tool.archive_orphans(registry, now=now)


def test_a_settled_orphan_moves_to_restore_and_a_running_demo_stays(tmp_path):
    root = _tree(tmp_path, ["roadmap", "station"], age=2 * HOUR)
    registry = {"demos": [_running(root, "station")]}

    moved, kept = _sweep(root, registry)

    assert moved == ["roadmap"] and kept == []
    assert not (root / "roadmap").exists()
    assert (root / "station" / "index.html").exists()
    parked = os.listdir(tmp_path / "workspace" / "restore" / "demos")
    assert len(parked) == 1 and parked[0].startswith("roadmap-")
    assert (tmp_path / "workspace" / "restore" / "demos" / parked[0] / "index.html").exists()


def test_an_orphan_touched_inside_one_turn_is_left_alone(tmp_path):
    """A cycle writes the files first and calls `start` after, so for a few
    minutes a real demo has no row."""
    root = _tree(tmp_path, ["draft", "station"], age=10 * 60)
    registry = {"demos": [_running(root, "station")]}

    moved, kept = _sweep(root, registry)

    assert moved == [] and kept == ["draft"]
    assert (root / "draft" / "index.html").exists()


def test_an_empty_registry_moves_nothing(tmp_path):
    root = _tree(tmp_path, ["station", "loop-metro"], age=2 * HOUR)

    moved, kept = _sweep(root, {"demos": []})

    assert moved == [] and kept == ["loop-metro", "station"]
    assert (root / "station").exists() and (root / "loop-metro").exists()


def test_reap_runs_the_sweep(tmp_path):
    registry = {"demos": []}
    args = type("A", (), {"idle": None, "no_restart": True, "unopened": 60})()
    with patch.object(demo_tool, "_read_registry", return_value=(registry, "/tmp/rev")), \
         patch.object(demo_tool, "pod_ip", return_value="10.42.0.1"), \
         patch.object(demo_tool, "archive_orphans", return_value=([], [])) as sweep:
        demo_tool.cmd_reap(args)
    sweep.assert_called_once_with(registry)
