"""Tests for `tools.persona_memory` --- idea #165's three stores.

The rule these are written against: **an unreadable store and an empty
store are different findings**, and the whole value of the tool is that it
never prints one when it means the other.
"""
import os
import time

import pytest

from tools import persona_memory as pm


def _dir(tmp_path, persona_id, files):
    d = tmp_path / persona_id
    d.mkdir()
    for name, body in files.items():
        (d / name).write_text(body)
    return d


def test_read_dirs_returns_none_when_the_root_is_absent(tmp_path):
    """Not `[]`. "The mount is not here" is not "nobody has written"."""
    assert pm.read_dirs(str(tmp_path / "nope")) is None


def test_read_dirs_counts_files_and_finds_the_index(tmp_path):
    _dir(tmp_path, "aaa", {"MEMORY.md": "- x", "user_x.md": "x"})
    _dir(tmp_path, "bbb", {})
    (tmp_path / "loose.txt").write_text("not a persona")

    rows = {r["persona_id"]: r for r in pm.read_dirs(str(tmp_path))}

    assert set(rows) == {"aaa", "bbb"}, "a loose file is not a persona"
    assert rows["aaa"]["files"] == 2
    assert rows["aaa"]["has_index"] is True
    assert rows["aaa"]["newest"] is not None
    assert rows["bbb"]["files"] == 0
    assert rows["bbb"]["has_index"] is False
    assert rows["bbb"]["newest"] is None


def test_read_dirs_flags_memories_written_without_an_index(tmp_path):
    """Recall reads MEMORY.md, so files with no index are half-written."""
    _dir(tmp_path, "aaa", {"user_x.md": "x"})
    row, = pm.read_dirs(str(tmp_path))
    assert row["files"] == 1
    assert row["has_index"] is False


def test_newest_is_the_newest_file_not_the_first(tmp_path):
    d = _dir(tmp_path, "aaa", {"old.md": "x", "new.md": "y"})
    os.utime(d / "old.md", (1_000_000, 1_000_000))
    os.utime(d / "new.md", (2_000_000, 2_000_000))
    row, = pm.read_dirs(str(tmp_path))
    assert row["newest"] == 2_000_000


def test_filled_does_not_count_whitespace():
    rows = [{"m": ""}, {"m": "   \n"}, {"m": None}, {}, {"m": "real"}]
    assert pm._filled(rows, "m") == 1


def test_all_three_empty_is_exit_2():
    """The state the idea was filed about. Anything else is not this."""
    dirs = [{"persona_id": "aaa", "files": 0, "has_index": False,
             "newest": None, "created": 1_000_000}]
    text, status = pm.judge(dirs, [{"id": "aaa", "name": "Claude"}], [])
    assert status == 2
    assert "All three stores are empty" in text


def test_a_written_file_store_is_exit_0_even_with_the_other_two_empty():
    """Today's live state: one persona writes, the two Agora fields do not."""
    dirs = [{"persona_id": "aaa", "files": 9, "has_index": True,
             "newest": 1_757_000_000, "created": 1_000_000}]
    personas = [{"id": "aaa", "name": "Nova", "sharedMemory": ""}]
    text, status = pm.judge(dirs, personas, [{"memory": ""}])
    assert status == 0
    assert "9 file(s)" in text
    assert "index present" in text
    assert "All three stores are empty" not in text


@pytest.mark.parametrize("personas, conversations", [
    ([{"id": "aaa", "name": "Claude", "sharedMemory": "remembered"}], []),
    ([{"id": "aaa", "name": "Claude"}], [{"memory": "remembered"}]),
])
def test_either_agora_store_alone_clears_exit_2(personas, conversations):
    """The rule is 'all three', not 'the file store'. Two mutations of the
    `not held and not shared and not notes` condition drop one term each;
    with only the file-store case tested, both would survive."""
    dirs = [{"persona_id": "aaa", "files": 0, "has_index": False,
             "newest": None, "created": 1_000_000}]
    text, status = pm.judge(dirs, personas, conversations)
    assert status == 0
    assert "All three stores are empty" not in text


def test_a_missing_root_is_exit_1_and_never_says_empty():
    text, status = pm.judge(None, [{"id": "a"}], [])
    assert status == 1
    assert "no instrument" in text
    assert "All three stores are empty" not in text
    assert "FILE MEMORY — EMPTY" not in text


def test_an_unreadable_agora_is_exit_1_and_never_says_empty():
    text, status = pm.judge([], [], [], error="could not read /personas: 500")
    assert status == 1
    assert "COULD NOT READ" in text
    assert "All three stores are empty" not in text


def test_a_directory_whose_persona_agora_does_not_know_is_still_listed():
    """A persona deleted from Agora leaves its memories behind; they are
    still on the PVC and still worth naming."""
    dirs = [{"persona_id": "ghost", "files": 3, "has_index": True,
             "newest": 1_757_000_000, "created": 1_000_000}]
    text, _ = pm.judge(dirs, [], [])
    assert "ghost" in text
    assert "?" in text


def test_stamps_are_oslo_not_utc():
    """Rule 7. 1757000000 is 2025-09-04T15:33:20Z, which is 17:33 in Oslo."""
    assert pm._oslo(1_757_000_000) == "2025-09-04 17:33 Oslo"
    assert time.gmtime(1_757_000_000).tm_hour == 15, "the UTC hour differs"
