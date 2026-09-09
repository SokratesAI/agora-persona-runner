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
import select
import subprocess
import sys
import tempfile
import time


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


DEFAULT_MAX_OUTPUT_BYTES = 2 * 1024 * 1024

#: Wide enough that a genuinely slow suite is never cut off, tight enough
#: that a stuck one leaves the cycle a working turn. The number to beat is
#: the 45-minute turn cap: a hang has to be killed with time left to write
#: a journal entry, or the diagnosis dies with the cycle the same way the
#: hang would have. Ten minutes is several times this repo's own suite and
#: leaves at least half a turn. Raise it with --timeout-seconds rather than
#: here if a particular command is honestly slower.
DEFAULT_TIMEOUT_SECONDS = 10 * 60


def run_bounded(command, max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    """Run the command, keeping only the head and the tail of what it prints.

    `subprocess.run(capture_output=True)` holds the whole of a child's
    output in one string, and a mutant is by definition code that is
    known to be wrong -- a wrong loop bound prints without end. On
    2026-09-09 at 02:15 Oslo that combination killed the bridge
    container: a throwaway script ran nine mutants of `cycle_postmortem`,
    one of them made pytest print without stopping, and this process
    reached 3.9 GiB of anonymous memory against the pod's 4 GiB limit.
    `memory.oom.group` is set on that cgroup, so the kernel killed every
    process in the container rather than the greedy one -- tini included
    -- and the cycle that ran it died mid-turn with no reply and no
    journal entry.

    So the bound belongs here, in the one place every mutation run goes
    through, rather than in each throwaway script that a later cycle
    writes from scratch. The head is kept because a collection error or
    an import failure prints there; the tail is kept because pytest's
    summary line lives there and `count_failures` reads it. What was
    dropped is stated in the middle rather than silently elided, because
    a verdict read off truncated output has to say it was truncated.

    stderr is merged into stdout so that one bound covers both. The two
    were concatenated anyway, and interleaved is the truer order.

    `timeout_seconds` is the other half of the same argument and it is a
    wall-clock deadline rather than an idle one. A mutant is code known
    to be wrong, and the two ways that costs something are printing
    without end (the bound above) and never finishing at all -- a
    deleted loop increment, a lock never released, a `while True` whose
    exit condition was the mutated line. Neither prints anything alarming
    and neither ever returns, so without a deadline the run holds the
    turn until the 45-minute cap kills the cycle: no reply, no journal
    entry. Pass `None` for no deadline.

    The kill is SIGKILL to the child this function started, and not to
    any process that child started in turn. The suite here is a single
    `pytest`, so that is the whole tree; a command that forks its own
    workers would leave them, and that is a scope limit rather than a
    thing to reason around.

    Returns `(returncode, text, total_bytes, dropped_bytes, timed_out)`.
    """
    half = max(1, max_output_bytes // 2)
    proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    head = bytearray()
    tail = bytearray()
    total = 0
    timed_out = False
    deadline = None if timeout_seconds is None \
        else time.monotonic() + timeout_seconds
    with proc.stdout as stream:
        fd = stream.fileno()
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                if not select.select([fd], [], [], remaining)[0]:
                    timed_out = True
                    break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if len(head) < half:
                take = half - len(head)
                head += chunk[:take]
                chunk = chunk[take:]
            if chunk:
                tail += chunk
                if len(tail) > half:
                    del tail[:len(tail) - half]
    if timed_out:
        proc.kill()
    returncode = proc.wait()
    dropped = total - len(head) - len(tail)
    parts = [bytes(head).decode("utf-8", "replace")]
    if dropped > 0:
        parts.append(
            "\n\n... %d byte(s) of output dropped by --max-output-bytes %d; "
            "the head and the tail are kept ...\n\n"
            % (dropped, max_output_bytes))
    parts.append(bytes(tail).decode("utf-8", "replace"))
    return returncode, "".join(parts), total, dropped, timed_out


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Break one file on purpose, run a command, restore the file.")
    parser.add_argument("--file", required=True,
                        help="the single file to mutate; nothing else is touched")
    parser.add_argument("--old", required=True,
                        help="literal text to replace; must occur exactly once")
    parser.add_argument("--new", required=True,
                        help="what to replace it with")
    parser.add_argument("--max-output-bytes", type=int,
                        default=DEFAULT_MAX_OUTPUT_BYTES,
                        help="how much of the command's output to keep in "
                             "memory; the head and the tail are kept and the "
                             "middle is dropped with a line saying how much")
    parser.add_argument("--timeout-seconds", type=float,
                        default=DEFAULT_TIMEOUT_SECONDS,
                        help="wall-clock deadline for the test command; a "
                             "mutant that hangs is killed and the round is "
                             "reported as TIMED OUT rather than graded. "
                             "0 means no deadline")
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
        returncode, output, total, dropped, timed_out = run_bounded(
            command, args.max_output_bytes,
            args.timeout_seconds if args.timeout_seconds else None)
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

    if dropped > 0:
        print("NOTE — the command printed %d bytes and only %d were kept. "
              "A mutant that prints without stopping is what this bound is "
              "for; the verdict below is read off the tail."
              % (total, total - dropped))

    if timed_out:
        print("TIMED OUT — the command was still running after %g second(s) "
              "and was killed, so this round is NOT a verdict. A killed "
              "command exits non-zero, which reads exactly like a caught "
              "mutation; it is not one, because the tests never finished. "
              "Raise --timeout-seconds if the suite is genuinely this slow, "
              "or find out what the mutation made hang."
              % args.timeout_seconds, file=sys.stderr)
        return 3

    failures = count_failures(output)
    if returncode != 0:
        detail = ("%d test(s) failed" % failures) if failures is not None \
            else "the command exited %d" % returncode
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
