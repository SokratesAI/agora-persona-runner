"""A roll input this cycle did not fetch is refused, and every read is named.

The failure this guards is quiet, which is why it is worth a file of its
own: `--live live.md` names a path in the runner checkout, every cycle
runs from that same checkout, and a copy left behind by an earlier cycle
reads exactly like one this cycle just fetched. Cycle 1673 rolled against
a `live.md` from the previous evening and was told `nothing to roll`,
exit 0, while the real digest was over its cap -- a stale read and a
genuine no-op say the same sentence.
"""

import os
import time

import pytest

from agora_runner.rolling import STALE_AFTER_MINUTES, RollError, read_roll_input
from tools import roll_digest, roll_handoff

LIVE = (
    "---\ntype: log\n---\n\n# Journal — Digest\n\n## Needs input\n\n"
    "**Nothing.**\n\n## Next cycle\n\n**[a] one.**\n\n## Digest\n\n"
    + "\n\n".join(
        f"**Cycle {n}** (2026-09-16 0{n % 10}:00) — line {n}." for n in range(1, 16)
    )
    + "\n"
)


def _age(path, minutes):
    old = time.time() - minutes * 60
    os.utime(path, (old, old))


def _write(tmp_path, name, text, minutes=0):
    path = tmp_path / name
    path.write_text(text)
    if minutes:
        _age(path, minutes)
    return path


def test_names_the_file_it_read(tmp_path, capsys):
    path = _write(tmp_path, "live.md", LIVE)
    read_roll_input(str(path))
    printed = capsys.readouterr().out
    assert str(path.resolve()) in printed
    assert "min ago" in printed


def test_refuses_a_file_older_than_this_cycle(tmp_path):
    path = _write(tmp_path, "live.md", LIVE, minutes=STALE_AFTER_MINUTES + 61)
    with pytest.raises(RollError) as excinfo:
        read_roll_input(str(path))
    message = str(excinfo.value)
    assert str(path.resolve()) in message
    assert "older than this cycle" in message


def test_a_fresh_file_is_read(tmp_path):
    path = _write(tmp_path, "live.md", LIVE)
    assert read_roll_input(str(path)) == LIVE


def test_max_age_zero_reads_it_anyway(tmp_path):
    path = _write(tmp_path, "live.md", LIVE, minutes=6000)
    assert read_roll_input(str(path), 0) == LIVE


def test_missing_file_is_the_callers_problem(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_roll_input(str(tmp_path / "nope.md"))


def test_roll_digest_refuses_a_stale_live_file_and_writes_nothing(tmp_path):
    live = _write(tmp_path, "live.md", LIVE, minutes=600)
    archive = _write(tmp_path, "archive.md", "", minutes=600)
    before = live.read_text()
    with pytest.raises(RollError):
        roll_digest.main(["--live", str(live), "--archive", str(archive)])
    assert live.read_text() == before
    assert archive.read_text() == ""


def test_roll_digest_still_rolls_a_fresh_pair(tmp_path, capsys):
    live = _write(tmp_path, "live.md", LIVE)
    archive = _write(tmp_path, "archive.md", "")
    assert roll_digest.main(["--live", str(live), "--archive", str(archive)]) == 0
    assert "roll off" in capsys.readouterr().out
    assert "**Cycle 15**" not in live.read_text()
    assert "**Cycle 15**" in archive.read_text()


def test_roll_digest_reads_a_stale_pair_when_told_to(tmp_path):
    live = _write(tmp_path, "live.md", LIVE, minutes=600)
    archive = _write(tmp_path, "archive.md", "", minutes=600)
    assert (
        roll_digest.main(
            ["--live", str(live), "--archive", str(archive), "--max-age-minutes", "0"]
        )
        == 0
    )
    assert "**Cycle 15**" in archive.read_text()


def test_roll_handoff_refuses_a_stale_live_file(tmp_path):
    live = _write(tmp_path, "live.md", LIVE, minutes=600)
    archive = _write(tmp_path, "handoff.md", "", minutes=600)
    with pytest.raises(RollError):
        roll_handoff.main(
            ["--live", str(live), "--archive", str(archive), "--retire-older-than-digest"]
        )


def test_roll_handoff_reads_a_fresh_live_file(tmp_path, capsys):
    live = _write(tmp_path, "live.md", LIVE)
    archive = _write(tmp_path, "handoff.md", "")
    assert roll_handoff.main(["--live", str(live), "--archive", str(archive)]) == 0
    assert "item(s) in **Next cycle**" in capsys.readouterr().out
