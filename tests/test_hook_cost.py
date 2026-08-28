"""Tests for tools.hook_cost.

Every cycle this tool has measured came back at about 1% of its input,
which is the answer idea #145 asked for and also means a green run proves
nothing on its own -- a check that cannot fail and a check that is passing
look identical from outside. So both failing cases are built explicitly:
steady drag above the threshold, and one oversized injection.

The other three tests pin the two things I got wrong while writing it --
counting transcript rows as if they were API responses, and judging
subagent transcripts that structurally cannot carry a hook.
"""

import json

import pytest

from tools import hook_cost


def write_transcript(root, name, rows):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return path


def hook_row(text, event="PostToolUse"):
    return {
        "type": "attachment",
        "attachment": {
            "type": "hook_additional_context",
            "hookEvent": event,
            "content": [text],
        },
    }


def turn_row(message_id, *, cache_read=100000):
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "role": "assistant",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def a_cycle(hook_text, turns, *, cache_read=100000, prefix="m"):
    """One hook injection, then `turns` responses that all carry it."""
    rows = [hook_row(hook_text)]
    for index in range(turns):
        rows.append(turn_row(f"{prefix}{index}", cache_read=cache_read))
    return rows


def test_ordinary_cycle_is_a_rounding_error(tmp_path, capsys):
    write_transcript(tmp_path, "cycle.jsonl", a_cycle("Clock: 01:39 Oslo", 80))
    assert hook_cost.main(["--root", str(tmp_path)]) == 0
    assert "Nothing to act on" in capsys.readouterr().out


def test_steady_drag_above_the_threshold_exits_2(tmp_path, capsys):
    # A hook line on every turn, against a small prompt: the shape of a
    # hook that has grown rather than one that blew up once.
    rows = []
    for index in range(80):
        rows.append(hook_row("x" * 400))
        rows.append(turn_row(f"m{index}", cache_read=2000))
    write_transcript(tmp_path, "cycle.jsonl", rows)
    assert hook_cost.main(["--root", str(tmp_path)]) == 2
    assert "HOOK OUTPUT IS MATERIAL" in capsys.readouterr().out


def test_one_oversized_injection_exits_2(tmp_path, capsys):
    # The 2.1.247 case: a hook printing unbounded error output. The share
    # stays tiny here on purpose -- this must be caught by size alone.
    rows = a_cycle("Clock: 01:39 Oslo", 80, cache_read=50_000_000)
    rows.insert(1, hook_row("E" * 50_000))
    write_transcript(tmp_path, "cycle.jsonl", rows)
    assert hook_cost.main(["--root", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "WEDGE RISK" in out
    assert "HOOK OUTPUT IS MATERIAL" not in out


def test_rows_sharing_one_message_id_count_as_one_turn(tmp_path):
    # A real response is written as several rows -- thinking, text, each
    # tool_use -- all carrying the same usage object. Counting rows
    # inflated one measured cycle from 86 turns to 154 and its input from
    # 11.5M tokens to 21.0M.
    rows = [hook_row("Clock")]
    for index in range(60):
        rows.append(turn_row(f"m{index}"))
        rows.append(turn_row(f"m{index}"))
        rows.append(turn_row(f"m{index}"))
    write_transcript(tmp_path, "cycle.jsonl", rows)
    measured = hook_cost.measure_session(tmp_path / "cycle.jsonl")
    assert measured["turns"] == 60
    assert measured["input_tokens"] == 60 * 100010


def test_subagent_transcripts_are_not_judged(tmp_path, capsys):
    write_transcript(tmp_path, "cycle.jsonl", a_cycle("Clock", 80))
    # Same content, but under the path the CLI gives a subagent. A
    # subagent runs no hooks, so judging one is a pass guaranteed in
    # advance; it must not pad the sample.
    write_transcript(tmp_path / "sub" / "subagents", "agent-a1.jsonl",
                     [turn_row(f"s{i}") for i in range(80)])
    assert hook_cost.main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Judged 1 cycle(s)" in out
    assert "1 subagent transcript(s) passed over" in out


def test_missing_transcript_root_is_unreadable_not_clean(tmp_path, capsys):
    assert hook_cost.main(["--root", str(tmp_path / "nowhere")]) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_no_recorded_usage_is_unknown_not_zero(tmp_path, capsys):
    rows = [hook_row("Clock")]
    for index in range(80):
        rows.append({"type": "assistant", "message": {"id": f"m{index}", "usage": {}}})
    write_transcript(tmp_path, "cycle.jsonl", rows)
    assert hook_cost.main(["--root", str(tmp_path)]) == 1
    assert "COULD NOT READ" in capsys.readouterr().out
