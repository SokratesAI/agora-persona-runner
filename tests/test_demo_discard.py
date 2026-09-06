"""`tools.demo discard` -- idea #139, "discard has to be as cheap as create".

`stop` kills the process, releases the port and drops the registry row. It
has never deleted a byte, so every demo ever handed over is still on disk.
The tests here go at the two things that can go wrong when a command
deletes a directory tree: **it deletes the wrong tree**, and **it deletes
the files out from under a process that is still serving them** -- which
is the exact hole `nova_demos.ephemeral_reason` exists to refuse, arrived
at from the other direction.
"""

import os
from unittest.mock import patch

from agora_runner import nova_demos
from tools import demo as demo_tool


def _args(slug="bakeoff"):
    return type("A", (), {"slug": slug})()


def _registry(**kw):
    row = {"slug": "bakeoff", "host": "10.42.0.56", "port": 5174,
           "dir": nova_demos.DURABLE_ROOT + "/bakeoff", "pid": 1234,
           "started_at": "2026-09-06T09:00:00"}
    row.update(kw)
    return {"demos": [row]}


def test_discard_deletes_the_directory_of_a_demo_it_stopped(tmp_path, capsys):
    root = tmp_path / "demos"
    (root / "bakeoff").mkdir(parents=True)
    (root / "bakeoff" / "index.html").write_text("<h1>hi</h1>")
    reg = _registry(dir=str(root / "bakeoff"))

    with patch.object(nova_demos, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "_read_registry", return_value=(reg, "/tmp/rev")), \
         patch.object(demo_tool, "_write_registry"), \
         patch.object(demo_tool, "cmd_stop", return_value=0):
        assert demo_tool.cmd_discard(_args()) == 0

    assert not (root / "bakeoff").exists()
    assert "deleted" in capsys.readouterr().out


def test_discard_deletes_a_directory_no_registry_row_claims(tmp_path, capsys):
    """The five orphans on disk are only reachable this way.

    Every demo stopped before this shipped left its files behind and its
    row is long gone, so a `discard` that required a registered slug could
    not remove any of them and the leak it closes would stay open.
    """
    root = tmp_path / "demos"
    (root / "roadmap").mkdir(parents=True)

    with patch.object(nova_demos, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "_read_registry", return_value=({"demos": []}, "/tmp/rev")):
        assert demo_tool.cmd_discard(_args("roadmap")) == 0

    assert not (root / "roadmap").exists()
    assert "was not registered" in capsys.readouterr().out


def test_discard_deletes_nothing_when_stop_could_not_stop_it(tmp_path, capsys):
    """`stop` returns 1 when the process is alive and unsignallable.

    Deleting the files then leaves the dev server answering out of a
    directory that no longer exists -- a link that 404s while the registry
    reads `running`, which is the failure `ephemeral_reason` was written
    for. So a non-zero `stop` has to stop this command too.
    """
    root = tmp_path / "demos"
    (root / "bakeoff").mkdir(parents=True)
    reg = _registry(dir=str(root / "bakeoff"))

    with patch.object(nova_demos, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "_read_registry", return_value=(reg, "/tmp/rev")), \
         patch.object(demo_tool, "cmd_stop", return_value=1):
        assert demo_tool.cmd_discard(_args()) == 1

    assert (root / "bakeoff" ).exists()


def test_discard_refuses_a_row_pointing_at_a_checkout(tmp_path, capsys):
    """A registry row may point anywhere: `start` only refuses the
    ephemeral roots, so `/data/workspace/agora-persona-runner` is a legal
    thing to have served and an illegal thing to delete."""
    root = tmp_path / "demos"
    root.mkdir(parents=True)
    checkout = tmp_path / "agora-persona-runner"
    checkout.mkdir()
    reg = _registry(dir=str(checkout))

    with patch.object(nova_demos, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "_read_registry", return_value=(reg, "/tmp/rev")), \
         patch.object(demo_tool, "_write_registry"), \
         patch.object(demo_tool, "cmd_stop", return_value=0):
        assert demo_tool.cmd_discard(_args()) == 2

    assert checkout.exists()
    assert "refusing to delete" in capsys.readouterr().err


def test_discard_is_not_an_error_when_the_files_are_already_gone(tmp_path, capsys):
    root = tmp_path / "demos"
    root.mkdir(parents=True)

    with patch.object(nova_demos, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "_read_registry", return_value=({"demos": []}, "/tmp/rev")):
        assert demo_tool.cmd_discard(_args("never-existed")) == 0

    assert "already gone" in capsys.readouterr().out


def test_list_names_the_orphan_directories(tmp_path, capsys):
    root = tmp_path / "demos"
    (root / "bakeoff").mkdir(parents=True)
    (root / "roadmap").mkdir(parents=True)
    reg = _registry(dir=str(root / "bakeoff"))

    with patch.object(nova_demos, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "DURABLE_ROOT", str(root)), \
         patch.object(demo_tool, "_read_registry", return_value=(reg, "/tmp/rev")), \
         patch.object(demo_tool, "fetch_activity", return_value=None), \
         patch.object(demo_tool, "pod_ip", return_value="10.42.0.56"), \
         patch.object(demo_tool, "pid_alive", return_value=True):
        demo_tool.cmd_list(_args())

    out = capsys.readouterr().out
    assert "roadmap" in out.split("belong to")[1]
    assert "bakeoff" not in out.split("belong to")[1]
