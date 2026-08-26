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
