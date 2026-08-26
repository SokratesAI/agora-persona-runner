"""Break one file on purpose, run the tests, put the file back exactly.

Cycle 454. This is the seventh time this loop has destroyed its own
uncommitted work undoing a mutation, and the first time the fix is a
tool rather than another paragraph. `playbooks/build-cycle.md` already
says, in bold, "copy the file aside and restore with `cp`, never
`git checkout`". Cycles 31, 122, 132, 152, 242, 437, 439, 450 and 451
each lost work anyway. A rule that has been restated six times and is
still being broken is not a rule anybody is failing to read; it is a
ritual that needs a machine.

**What actually goes wrong is always the pathspec, never the intent.**
`git checkout -- agora_runner/` reverts the whole implementation rather
than the mutated line (Cycle 242, whose remaining five mutations then
ran against a repo where the feature did not exist and all five looked
*stronger* for it). `git checkout <file>` reverts to the last commit,
which silently eats anything written since it (Cycle 152). And Cycle
451 committed first, ran six mutations clean, then wrote a seventh fix
*during* the round -- a round is not one event, so a commit taken
before it protects nothing written inside it.

So this tool never runs git at all. It snapshots the one file's exact
bytes at mutation time, into a temp directory outside the repo, and
restores those bytes when the test command is done -- on success, on
failure, on an exception, and on SIGINT/SIGTERM. Nothing else in the
tree is readable to it, so an edit made anywhere else during the round
cannot be reverted by it. That removes the whole class rather than
guarding one more instance of it, which is `prompt.md`'s rule about
three fixes of the same shape.

    python3 -m tools.mutate --file agora_runner/nova_journal.py \\
        --old 'if seq is None:' --new 'if False:' \\
        -- python3 -m pytest tests/test_nova_journal.py -q

**The mutation must match exactly once.** Two matches means the tool
cannot tell you which line your tests caught, and zero means you
mutated nothing and the green result is the "negative result guaranteed
in advance" failure from `prompt.md` wearing a lab coat. Both refuse
before anything is written.

Exit status is the mutation verdict, and it is deliberately inverted
from what a test runner returns, because the thing being judged here is
the test suite and not the code: **0 means the mutation was caught**
(the command failed, which is the good outcome), **2 means it survived**
(the command passed with the code broken -- those tests pin nothing),
and 1 means the check could not be run at all. A cycle can loop over a
set of mutations and treat any non-zero as a finding.
"""

import argparse
import glob
import os
import signal
import subprocess
import sys
import tempfile


def drop_bytecode(path):
    """Delete the cached `.pyc` beside a Python source file.

    Found by running this tool against itself, and it is the failure the
    tool exists to catch happening inside the tool. CPython decides a
    cached `.pyc` is current by comparing the source's **size and mtime
    in whole seconds** -- equality on both, nothing else. A mutation that
    swaps one operator for another of the same width leaves the size
    identical, and if it is written in the same second the module was
    last compiled, the interpreter loads the *unmutated* bytecode and the
    suite passes against code that was never broken.

    That is not a rare race. `!= 0` -> `>= 0` on this file survived three
    runs in a row and then got CAUGHT on the fourth, purely on which side
    of a second boundary the write landed. A mutation checker that
    sometimes does not mutate reports "SURVIVED" -- it accuses the tests
    of pinning nothing when the tests never saw the change, which is
    exactly the "negative result guaranteed in advance" trap from
    `prompt.md` with the sign flipped.

    So the cache is dropped after every write, the mutation and the
    restore alike. A missing `.pyc` is always safe: the interpreter
    recompiles.
    """
    directory, name = os.path.split(os.path.abspath(path))
    stem, ext = os.path.splitext(name)
    if ext != ".py":
        return
    for cached in glob.glob(os.path.join(directory, "__pycache__", stem + ".*.pyc")):
        try:
            os.remove(cached)
        except OSError:
            pass


class Restorer:
    """Holds one file's original bytes and puts them back, once.

    Restoring is idempotent on purpose. The `finally` path and the
    signal handler can both fire for a single run -- SIGTERM during the
    test command raises through the `with` block after the handler has
    already restored -- and a second write of the same bytes must not be
    an error or a second warning.
    """

    def __init__(self, path, snapshot_dir):
        self.path = path
        self.original = open(path, "rb").read()
        self.snapshot = os.path.join(snapshot_dir, os.path.basename(path) + ".safe")
        with open(self.snapshot, "wb") as fh:
            fh.write(self.original)
        self.mutated = None
        self.done = False
        self.clobbered = None

    def write_mutation(self, data):
        with open(self.path, "wb") as fh:
            fh.write(data)
        drop_bytecode(self.path)
        self.mutated = data

    def restore(self):
        """Put the original bytes back, and say so if something else moved.

        The one case where restoring could itself destroy work is a test
        command that edits the file under test. That is rare and it is
        not hypothetical -- a formatter in a pre-test hook would do it --
        so the on-disk content is compared against what was written
        before it is overwritten, and anything unexpected is kept beside
        the snapshot rather than thrown away.
        """
        if self.done:
            return
        self.done = True
        try:
            current = open(self.path, "rb").read()
        except OSError:
            current = None
        if current is not None and self.mutated is not None and current != self.mutated:
            self.clobbered = self.snapshot + ".observed"
            with open(self.clobbered, "wb") as fh:
                fh.write(current)
        with open(self.path, "wb") as fh:
            fh.write(self.original)
        drop_bytecode(self.path)

    def verify(self):
        return open(self.path, "rb").read() == self.original


def count_failures(text):
    """Pull pytest's failure count out of its summary line, if it is there.

    Best effort and clearly labelled as such at the call site. The whole
    point of "say the number in the journal" is that a count is
    checkable and "mutation-checked" is not, so the tool offers the
    number when it can read one and stays quiet rather than guessing
    when it cannot -- a fabricated count is worse than no count.
    """
    for line in reversed(text.strip().splitlines()):
        if " failed" not in line:
            continue
        parts = line.replace("=", " ").split()
        for i, word in enumerate(parts):
            if word.startswith("failed") and i:
                try:
                    return int(parts[i - 1])
                except ValueError:
                    return None
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Break one file on purpose, run a command, restore the file.")
    parser.add_argument("--file", required=True,
                        help="the single file to mutate; nothing else is touched")
    parser.add_argument("--old", required=True,
                        help="literal text to replace; must occur exactly once")
    parser.add_argument("--new", required=True,
                        help="what to replace it with")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="-- followed by the test command to run")
    args = parser.parse_args(argv)

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("no test command given — put it after a bare `--`", file=sys.stderr)
        return 1

    if not os.path.isfile(args.file):
        print("no such file: %s" % args.file, file=sys.stderr)
        return 1

    original = open(args.file, "rb").read()
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError:
        print("not a utf-8 text file: %s" % args.file, file=sys.stderr)
        return 1

    hits = text.count(args.old)
    if hits == 0:
        print("--old does not appear in %s, so there is no mutation to run. "
              "A green result here would prove nothing." % args.file,
              file=sys.stderr)
        return 1
    if hits > 1:
        print("--old appears %d times in %s. Give a longer, unique string: "
              "with more than one site mutated, a red suite cannot tell you "
              "which one your tests caught." % (hits, args.file), file=sys.stderr)
        return 1

    snapshot_dir = tempfile.mkdtemp(prefix="nova-mutate-")
    restorer = Restorer(args.file, snapshot_dir)

    def on_signal(signum, _frame):
        restorer.restore()
        raise KeyboardInterrupt

    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, on_signal)

    print("mutating %s (1 site), snapshot at %s" % (args.file, restorer.snapshot))
    try:
        restorer.write_mutation(text.replace(args.old, args.new).encode("utf-8"))
        proc = subprocess.run(command, capture_output=True, text=True)
        output = (proc.stdout or "") + (proc.stderr or "")
        sys.stdout.write(output)
    finally:
        restorer.restore()
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    if not restorer.verify():
        print("RESTORE FAILED — %s does not match the snapshot. The original "
              "bytes are at %s; put them back by hand before doing anything "
              "else." % (args.file, restorer.snapshot), file=sys.stderr)
        return 1
    print("restored %s" % args.file)
    if restorer.clobbered:
        print("NOTE — the command changed %s while it ran. What it left is "
              "saved at %s; the original is back in place."
              % (args.file, restorer.clobbered))

    failures = count_failures(output)
    if proc.returncode != 0:
        detail = ("%d test(s) failed" % failures) if failures is not None \
            else "the command exited %d" % proc.returncode
        print("CAUGHT — %s with the mutation in place. Say the number in the "
              "journal; a count is checkable and \"mutation-checked\" is not."
              % detail)
        return 0

    print("SURVIVED — the command passed with %s broken. Those tests pin "
          "nothing about this line. Find out why before going further: a "
          "control that agrees with you is a broken control." % args.file)
    return 2


if __name__ == "__main__":
    sys.exit(main())
