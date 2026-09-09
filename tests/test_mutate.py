"""Tests for tools.mutate.

The one that matters is `test_an_edit_made_during_the_round_survives`.
That is Cycle 451's actual loss, reproduced: a second file edited while
the mutation was running, which `git checkout` took and this tool must
not. Everything else here guards a refusal — a mutation that matched
nothing or matched twice is a check whose result was decided before it
ran, and both of those have shipped in this journal as evidence.
"""

import os
import sys
import time

import pytest

from tools import mutate


TARGET = "def flag():\n    return True\n"


@pytest.fixture
def repo(tmp_path):
    """A two-file tree: one to mutate, one that must never be touched."""
    (tmp_path / "subject.py").write_text(TARGET)
    (tmp_path / "bystander.py").write_text("original\n")
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)


def run(old, new, *command):
    return mutate.main(["--file", "subject.py", "--old", old, "--new", new,
                        "--", *command])


def test_a_mutation_the_command_catches_exits_zero(repo, capsys):
    """A red command is the good outcome, so it is exit 0, not exit 1."""
    code = run("return True", "return False", sys.executable, "-c", "raise SystemExit(1)")
    assert code == 0
    assert "CAUGHT" in capsys.readouterr().out


def test_a_mutation_the_command_misses_exits_two(repo, capsys):
    code = run("return True", "return False", sys.executable, "-c", "pass")
    assert code == 2
    assert "SURVIVED" in capsys.readouterr().out


def test_the_file_is_mutated_while_the_command_runs(repo):
    """Otherwise the whole exercise is theatre — Cycle 196's failure."""
    reader = "import pathlib; print(pathlib.Path('subject.py').read_text())"
    mutate.main(["--file", "subject.py", "--old", "return True",
                 "--new", "return False", "--", sys.executable, "-c", reader])
    # The command saw the mutation; the tree is back to the original.
    assert (repo / "subject.py").read_text() == TARGET


def test_the_file_is_restored_when_the_command_fails(repo):
    run("return True", "return False", sys.executable, "-c", "raise SystemExit(3)")
    assert (repo / "subject.py").read_text() == TARGET


def test_an_edit_made_during_the_round_survives(repo):
    """Cycle 451, exactly: a fix written mid-round must not be reverted.

    `git checkout -- <dir>` took this and `git checkout <file>` took the
    version of it since the last commit. A byte-for-byte restore of one
    file cannot reach it at all, which is why the tool never runs git.
    """
    writer = "import pathlib; pathlib.Path('bystander.py').write_text('a real fix\\n')"
    mutate.main(["--file", "subject.py", "--old", "return True",
                 "--new", "return False", "--", sys.executable, "-c", writer])
    assert (repo / "bystander.py").read_text() == "a real fix\n"
    assert (repo / "subject.py").read_text() == TARGET


def test_a_mutation_that_matches_nothing_refuses(repo, capsys):
    code = run("return Maybe", "return False", sys.executable, "-c", "pass")
    assert code == 1
    assert "no mutation to run" in capsys.readouterr().err
    assert (repo / "subject.py").read_text() == TARGET


def test_a_mutation_that_matches_twice_refuses(repo, capsys):
    (repo / "subject.py").write_text(TARGET + TARGET)
    code = run("return True", "return False", sys.executable, "-c", "pass")
    assert code == 1
    assert "appears 2 times" in capsys.readouterr().err
    assert (repo / "subject.py").read_text() == TARGET + TARGET


def test_a_command_that_rewrites_the_subject_keeps_what_it_wrote(repo, capsys):
    """Restoring must not itself be a way to lose work."""
    writer = "import pathlib; pathlib.Path('subject.py').write_text('formatted\\n')"
    mutate.main(["--file", "subject.py", "--old", "return True",
                 "--new", "return False", "--", sys.executable, "-c", writer])
    out = capsys.readouterr().out
    assert "changed subject.py while it ran" in out
    observed = out.split("saved at ")[1].split(";")[0].strip()
    assert open(observed).read() == "formatted\n"
    assert (repo / "subject.py").read_text() == TARGET


def test_no_command_is_an_error_not_a_silent_pass(repo, capsys):
    code = mutate.main(["--file", "subject.py", "--old", "x", "--new", "y"])
    assert code == 1
    assert "no test command" in capsys.readouterr().err


def test_the_failure_count_comes_off_pytests_own_summary():
    assert mutate.count_failures("=== 4 failed, 871 passed in 2.11s ===") == 4
    assert mutate.count_failures("=== 875 passed in 2.11s ===") is None
    assert mutate.count_failures("") is None


def test_a_same_size_mutation_actually_reaches_the_command(repo):
    """The trap the tool found in itself: a stale `.pyc` hiding the mutation.

    CPython keys its bytecode cache on the source's size and its mtime in
    whole seconds. A one-token mutation of the same width changes
    neither, so a run landing in the same second as the last compile
    imports the *original* bytecode and reports SURVIVED against code
    that was never broken. The subject is compiled first here to set that
    cache up, and the command fails when it sees the mutation — so a
    CAUGHT verdict is the proof the new bytes were the ones imported.
    """
    import compileall

    (repo / "subject.py").write_text("VALUE = 111\n")
    compileall.compile_file(str(repo / "subject.py"), quiet=1)
    assert list((repo / "__pycache__").glob("subject.*.pyc"))

    saw_it = "import subject; raise SystemExit(7 if subject.VALUE == 999 else 0)"
    code = mutate.main(["--file", "subject.py", "--old", "111", "--new", "999",
                        "--", sys.executable, "-c", saw_it])
    assert code == 0, "the command imported stale bytecode, not the mutation"
    assert (repo / "subject.py").read_text() == "VALUE = 111\n"


def test_drop_bytecode_leaves_a_non_python_file_alone(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("x")
    mutate.drop_bytecode(str(target))  # must not raise
    assert target.read_text() == "x"


# --- the output bound -------------------------------------------------
#
# Cycle 1254. `subprocess.run(capture_output=True)` held everything a
# mutant printed, and on 2026-09-09 at 02:15 Oslo one that printed
# without stopping took this process to 3.9 GiB and the kernel killed
# every process in the bridge container with it. The invariant these
# pin is the one that stops that: what the tool keeps is bounded by
# --max-output-bytes no matter what the child prints, and the verdict
# is still read off the tail.


def _noisy(byte_count, exit_code=0):
    """A command that prints `byte_count` bytes of output and exits."""
    return [sys.executable, "-c",
            "import sys;sys.stdout.write('first line\\n');"
            "sys.stdout.write('x' * %d);"
            "sys.stdout.write('\\nlast line\\n');raise SystemExit(%d)"
            % (byte_count, exit_code)]


def test_output_under_the_bound_is_kept_whole():
    code, text, total, dropped, timed_out = mutate.run_bounded(
        [sys.executable, "-c", "print('hello')"], max_output_bytes=4096)
    assert timed_out is False
    assert code == 0
    assert dropped == 0
    assert text == "hello\n"
    assert total == len(b"hello\n")


def test_output_over_the_bound_is_truncated_not_held():
    """The whole point: 200x the bound printed, and the bound holds."""
    code, text, total, dropped, timed_out = mutate.run_bounded(
        _noisy(200_000), max_output_bytes=1000)
    assert timed_out is False
    assert code == 0
    assert total > 200_000
    assert dropped > 199_000
    # The kept text is the bound plus the one line that says what went.
    assert len(text) < 1000 + 200
    # Both ends are kept: the head is where a collection error prints,
    # the tail is where the summary line is.
    assert text.startswith("first line\n")
    assert text.rstrip().endswith("last line")
    assert "dropped by --max-output-bytes" in text


def test_the_summary_line_survives_truncation(repo, capsys):
    """`count_failures` reads the tail, so the count must still be there."""
    command = [sys.executable, "-c",
               "import sys;sys.stdout.write('x' * 300000);"
               "print('\\n= 7 failed, 2 passed =');raise SystemExit(1)"]
    code = mutate.main(["--file", "subject.py", "--old", "return True",
                        "--new", "return False", "--max-output-bytes", "2000",
                        "--", *command])
    out = capsys.readouterr().out
    assert code == 0
    assert "CAUGHT — 7 test(s) failed" in out
    assert "NOTE — the command printed 300" in out


def test_no_note_when_nothing_was_dropped(repo, capsys):
    code = run("return True", "return False", *_noisy(10, exit_code=1))
    assert code == 0
    assert "NOTE — the command printed" not in capsys.readouterr().out


def test_stderr_is_captured_too(repo, capsys):
    code = run("return True", "return False", sys.executable, "-c",
               "import sys;sys.stderr.write('boom\\n');raise SystemExit(1)")
    assert code == 0
    assert "boom" in capsys.readouterr().out


def test_a_command_that_hangs_is_killed_rather_than_held():
    """The failure this deadline is for: a mutant that never returns.

    Without it `run_bounded` blocks in `os.read` until the 45-minute turn
    cap kills the whole cycle -- no reply, no journal entry, which is one
    of the shapes idea #267 is counting.
    """
    started = time.monotonic()
    code, text, total, dropped, timed_out = mutate.run_bounded(
        [sys.executable, "-c", "import time;time.sleep(120)"],
        timeout_seconds=1.0)
    elapsed = time.monotonic() - started
    assert timed_out is True
    assert elapsed < 30, "it waited for the child instead of killing it"
    assert code != 0


def test_a_command_that_prints_without_stopping_hits_the_same_deadline():
    """The deadline is wall-clock, not idle.

    A runaway that prints keeps the pipe readable forever, so an
    idle-timeout would never fire on the very case the memory bound was
    built for. Only a deadline covers both.
    """
    started = time.monotonic()
    code, text, total, dropped, timed_out = mutate.run_bounded(
        [sys.executable, "-c",
         "import sys\nwhile True: sys.stdout.write('x' * 4096)"],
        max_output_bytes=1000, timeout_seconds=1.0)
    elapsed = time.monotonic() - started
    assert timed_out is True
    assert elapsed < 30
    assert total > 100_000, "the child was never actually printing"


def test_a_run_inside_the_deadline_is_not_reported_as_timed_out():
    """The precondition for the two tests above: this deadline can pass."""
    code, text, total, dropped, timed_out = mutate.run_bounded(
        [sys.executable, "-c", "print('quick')"], timeout_seconds=60)
    assert timed_out is False
    assert code == 0
    assert text == "quick\n"


def test_a_timed_out_round_is_not_graded_as_caught(repo, capsys):
    """A killed command exits non-zero and that is NOT a caught mutation.

    This is the whole reason the flag exists rather than the exit code
    being read: `proc.kill()` gives 137, `count_failures` finds nothing,
    and the old grading would have printed CAUGHT -- a control that
    agrees with you because it never ran.
    """
    code = mutate.main(["--file", "subject.py", "--old", "True",
                        "--new", "False", "--timeout-seconds", "1",
                        "--", sys.executable, "-c",
                        "import time;time.sleep(120)"])
    out = capsys.readouterr()
    assert code == 3
    assert "TIMED OUT" in out.err
    assert "CAUGHT" not in out.out
    assert "SURVIVED" not in out.out
    # The file still has to come back, same as any other round.
    assert (repo / "subject.py").read_text() == TARGET


def test_zero_seconds_means_no_deadline(repo, capsys):
    """The escape hatch, because a genuinely slow suite must stay runnable."""
    code = mutate.main(["--file", "subject.py", "--old", "True",
                        "--new", "False", "--timeout-seconds", "0",
                        "--", sys.executable, "-c",
                        "import time;time.sleep(0.2);raise SystemExit(1)"])
    out = capsys.readouterr()
    assert code == 0
    assert "CAUGHT" in out.out
    assert "TIMED OUT" not in out.err
