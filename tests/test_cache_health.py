"""Tests for tools.cache_health.

The one that matters is `test_broken_caching_exits_2`. Every day this
tool has ever measured came back healthy, so a green run proves nothing
on its own -- a check that cannot fail and a check that is passing look
identical from outside. So the failing case is built explicitly, out of
the exact shape a caching loss produces: fresh input where the cache
reads should be.
"""

import json

import pytest

from tools import cache_health


def write_transcript(root, name, rows):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return path


def usage_row(day, *, input_tokens, cache_read, cache_create=0, output=100):
    return {
        "timestamp": f"{day}T12:00:00.000Z",
        "message": {
            "role": "assistant",
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_create,
                "cache_read_input_tokens": cache_read,
                "output_tokens": output,
            },
        },
    }


@pytest.fixture
def root(tmp_path, monkeypatch):
    directory = tmp_path / "projects"
    directory.mkdir()
    monkeypatch.setenv("NOVA_TRANSCRIPT_ROOT", str(directory))
    for name in cache_health.ROUTING_VARS:
        monkeypatch.delenv(name, raising=False)
    return directory


def run(argv):
    return cache_health.main(argv)


def healthy_day(root, day, count=250):
    """What this loop actually looks like: ~97% of input read from cache."""
    write_transcript(
        root, f"session-{day}/transcript.jsonl",
        [usage_row(day, input_tokens=3, cache_read=100_000, cache_create=3_000)] * count,
    )


def test_healthy_days_exit_0(root, capsys):
    healthy_day(root, "2026-08-25")
    healthy_day(root, "2026-08-26")
    assert run(["--now", "2026-08-27", "--days", "7"]) == 0
    out = capsys.readouterr().out
    assert "Caching healthy on all 2 day(s) judged" in out
    assert "no gateway, proxy or custom base URL set" in out


def test_broken_caching_exits_2(root, capsys):
    """A day where every turn re-sent the prompt as fresh input."""
    healthy_day(root, "2026-08-25")
    write_transcript(
        root, "session-broken/transcript.jsonl",
        [usage_row("2026-08-26", input_tokens=100_000, cache_read=0)] * 250,
    )
    assert run(["--now", "2026-08-27", "--days", "7"]) == 2
    out = capsys.readouterr().out
    assert "CACHING DEGRADED on 1 of 2 day(s) judged" in out
    assert "2026-08-26" in out
    assert "BELOW THRESHOLD" in out


def test_today_is_not_judged(root, capsys):
    """A day in progress is a partial sample, so it never sets the status."""
    write_transcript(
        root, "session-today/transcript.jsonl",
        [usage_row("2026-08-27", input_tokens=100_000, cache_read=0)] * 250,
    )
    assert run(["--now", "2026-08-27", "--days", "7"]) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_short_day_is_skipped_not_failed(root, capsys):
    """Too few messages is 'not judged', never 'degraded'."""
    healthy_day(root, "2026-08-25")
    write_transcript(
        root, "session-quiet/transcript.jsonl",
        [usage_row("2026-08-26", input_tokens=100_000, cache_read=0)] * 3,
    )
    assert run(["--now", "2026-08-27", "--days", "7"]) == 0
    out = capsys.readouterr().out
    assert "too few messages" in out
    assert "2026-08-26" in out


def test_missing_transcripts_exit_1(tmp_path, monkeypatch, capsys):
    """No CLI on this pod reads as 'could not check', never as clean."""
    monkeypatch.setenv("NOVA_TRANSCRIPT_ROOT", str(tmp_path / "nothing-here"))
    assert run(["--now", "2026-08-27"]) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_gateway_is_reported(root, monkeypatch, capsys):
    """The cause the 2.1.237 release names is printed beside the ratio."""
    healthy_day(root, "2026-08-26")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example/v1")
    assert run(["--now", "2026-08-27"]) == 0
    assert "routed through:  ANTHROPIC_BASE_URL" in capsys.readouterr().out


def test_days_window_excludes_older_days(root, capsys):
    healthy_day(root, "2026-08-26")
    write_transcript(
        root, "session-old/transcript.jsonl",
        [usage_row("2026-08-01", input_tokens=100_000, cache_read=0)] * 250,
    )
    assert run(["--now", "2026-08-27", "--days", "7"]) == 0
    assert "2026-08-01" not in capsys.readouterr().out


def test_cache_share_is_a_ratio_not_a_total():
    """Volume must not move the number the threshold reads."""
    small = {"input": 3, "cache_create": 3_000, "cache_read": 100_000}
    large = {k: v * 40 for k, v in small.items()}
    assert cache_health.cache_share(small) == pytest.approx(
        cache_health.cache_share(large))
