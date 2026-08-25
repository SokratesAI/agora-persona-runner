"""Tests for tools.cli_pin.

The two that matter are the ones the live mutation check exposed: that a
gap is counted off `versions` rather than `time`, and that the exit
status separates "current", "behind but inside the window", "stale" and
"could not read" instead of collapsing any pair of them.
"""

import io
import json
from datetime import datetime, timezone

import pytest

from tools import cli_pin


NOW = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)


def fake_registry(latest, versions, times):
    """Stand in for the packument endpoint."""
    body = json.dumps(
        {"dist-tags": {"latest": latest},
         "versions": {v: {} for v in versions},
         "time": times}
    ).encode()

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return lambda request, timeout=None: Response(body)


@pytest.fixture
def dockerfile(tmp_path, monkeypatch):
    """A bridge checkout under NOVA_WORKSPACE, pin settable per test."""
    bridge = tmp_path / "agora-claude-bridge"
    bridge.mkdir()
    monkeypatch.setenv("NOVA_WORKSPACE", str(tmp_path))

    def write(pin):
        (bridge / "Dockerfile").write_text(
            f"FROM python:3.12-slim\nARG CLAUDE_CODE_VERSION={pin}\n"
            "RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}\n"
        )
    return write


def run(monkeypatch, latest, versions, times, running=None, argv=None):
    monkeypatch.setattr(
        cli_pin, "fetch_registry",
        lambda: (latest, {v: {} for v in versions}, times, None),
    )
    monkeypatch.setattr(cli_pin, "running_version", lambda: running)
    return cli_pin.main(argv or [], now=NOW)


def test_current_pin_exits_zero(dockerfile, monkeypatch, capsys):
    dockerfile("2.1.245")
    assert run(monkeypatch, "2.1.245", ["2.1.244", "2.1.245"], {}) == 0
    assert "The pin is current" in capsys.readouterr().out


def test_stale_pin_exits_two_and_counts_the_gap(dockerfile, monkeypatch, capsys):
    dockerfile("2.1.226")
    code = run(
        monkeypatch, "2.1.245",
        ["2.1.226", "2.1.230", "2.1.243", "2.1.245"],
        {"2.1.226": "2026-08-08T01:53:22.182Z"},
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "3 release(s) behind" in out, out
    assert "17.5 day(s) ago" in out, out
    assert out.startswith("@anthropic-ai/claude-code:")


def test_behind_but_inside_the_window_exits_zero(dockerfile, monkeypatch, capsys):
    """The CLI publishes most weekdays; a day-old gap is not a finding."""
    dockerfile("2.1.244")
    code = run(
        monkeypatch, "2.1.245", ["2.1.244", "2.1.245"],
        {"2.1.244": "2026-08-24T12:00:00.000Z"},
    )
    assert code == 0
    assert "inside the window" in capsys.readouterr().out


def test_unknown_publish_date_fails_towards_noise(dockerfile, monkeypatch, capsys):
    """The bug the live mutation check caught, pinned.

    An empty `time` map is what npm's abbreviated packument returns, and
    it must never read as "the pin is fine".
    """
    dockerfile("2.1.226")
    code = run(monkeypatch, "2.1.245", ["2.1.226", "2.1.245"], {})
    assert code == 2
    assert "STALE, ASSUMED" in capsys.readouterr().out


def test_gap_is_counted_off_versions_not_times(dockerfile, monkeypatch, capsys):
    """`time` carries created/modified and unpublished versions.

    Counting off it inflates the gap and can crash on the two non-version
    keys, so the count comes from `versions`.
    """
    dockerfile("2.1.226")
    run(
        monkeypatch, "2.1.245", ["2.1.226", "2.1.245"],
        {"created": "2025-01-01T00:00:00.000Z",
         "modified": "2026-08-25T05:12:38.211Z",
         "2.1.226": "2026-08-08T01:53:22.182Z",
         "2.1.240": "2026-08-20T00:00:00.000Z",
         "2.1.245": "2026-08-25T04:45:52.102Z"},
    )
    assert "1 release(s) behind" in capsys.readouterr().out


def test_running_binary_disagreeing_with_the_pin_is_called_out(
    dockerfile, monkeypatch, capsys
):
    dockerfile("2.1.245")
    run(monkeypatch, "2.1.245", ["2.1.245"], {}, running="2.1.226")
    out = capsys.readouterr().out
    assert "the running binary is 2.1.226, not the pinned 2.1.245" in out


def test_missing_pin_line_exits_one_rather_than_zero(dockerfile, monkeypatch, capsys):
    """Unreadable must never read as clean -- same contract as security_alerts."""
    dockerfile("2.1.245")
    path = cli_pin.dockerfile_candidates()[0]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("FROM python:3.12-slim\n")
    monkeypatch.setattr(cli_pin, "fetch_registry",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("registry must not be reached")))
    assert cli_pin.main([], now=NOW) == 1
    assert "COULD NOT READ THE PIN" in capsys.readouterr().out


def test_unreachable_registry_exits_one(dockerfile, monkeypatch, capsys):
    dockerfile("2.1.245")
    monkeypatch.setattr(cli_pin, "fetch_registry",
                        lambda: (None, None, None, "could not reach the npm registry: boom"))
    assert cli_pin.main([], now=NOW) == 1
    assert "COULD NOT READ THE REGISTRY" in capsys.readouterr().out


def test_fetch_registry_does_not_send_the_abbreviated_header():
    """The abbreviated packument drops `time`, which is the field we need."""
    seen = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout=None):
        seen["headers"] = dict(request.header_items())
        return Response(json.dumps(
            {"dist-tags": {"latest": "2.1.245"}, "versions": {}, "time": {}}
        ).encode())

    latest, versions, times, error = cli_pin.fetch_registry(opener=opener)
    assert error is None and latest == "2.1.245"
    assert not any("install-v1" in str(v) for v in seen["headers"].values())


def test_version_key_refuses_to_guess_at_a_prerelease():
    assert cli_pin.version_key("2.1.245") == (2, 1, 245)
    assert cli_pin.version_key("2.1.245-rc.1") is None
    assert cli_pin.releases_between("2.1.245-rc.1", "2.1.245", {"2.1.245": {}}) is None
