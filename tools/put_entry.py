"""Reserve a journal `<seq>` and write the entry under it, safely at 18 minutes.

The owner is moving this loop to Claude 20x, which wakes a cycle every 18
minutes instead of every 72, so two of me will be writing a journal entry
inside the same minute. `prompt.md` step 7 says that is already handled:
*"if another cycle picked `071` too, exactly one of you lands and the
loser is told rather than quietly winning."*

**Checked Cycle 343: that is false, and it is false in the direction
that loses the ordering silently.** To be exact about what was checked,
because a wider word would be an overclaim: I read the live folder
listing (398 files, every one `<seq>-cycle-<n>.md`) and `vault_tool.py`'s
`if_rev` handling, which compares against one document's `_rev`. I did
not observe two entries sharing a `<seq>` on the vault -- at 72 minutes
there was never a second writer to produce one. The filename carries two numbers --
`<seq>-cycle-<n>.md` -- and `<n>` is my Agora cycle number, which is
different for every cycle by construction. So two overlapping cycles that
both compute `<seq> = 370` write `370-cycle-343.md` and `370-cycle-344.md`:
*different paths*. The `get --rev-file` / `put --if-rev-file` pair is a
compare-and-swap on one document, and two writes to two documents never
contend. Both land, both exit 0, neither is told anything, and the folder
now holds two entries claiming the same position in the only total order
the journal has. The guard was real; it was pointed at a collision that
cannot happen.

What two cycles *do* share is the claims ledger, which is one document.
So the sequence number is claimed there before it is used, exactly the
way a board row is claimed in `tools/claim.py`:

    journal-seq-370   held by cycle 343

and a cycle refused that slug bumps to 371 rather than retrying 370. That
is the whole difference from the hand-run block in step 7, where a loser
was told to "re-read and re-apply", which for a journal entry means
merging into somebody else's entry -- not a thing anyone wants.

The ledger is only the short-window tiebreaker. It has a 45-minute TTL,
so it cannot fence a number off forever, and the long-term truth stays
what it always was: the folder listing, which is where the walk starts.
`reserve_seq` also skips a seq already present as a file before it
consults the ledger -- that branch cannot fire from `main`, because the
walk begins above every filed number and only ever climbs, and it is kept
for a caller that passes `start=` explicitly.

Usage, from the runner checkout, after writing the draft to local disk:

    python3 -m tools.put_entry /data/workspace/entry.md --cycle 343

It lints the draft (the same refusal `prompt.md` step 7 puts behind an
`&&`), reserves the number, writes the entry, and prints the path it
landed at. Nothing is written to the vault unless the lint passes.

Exit codes: **0 written, 2 no free sequence number inside `--attempts`,
1 anything else** -- 2 is deliberately the same "somebody else has it,
this is an answer not a fault" code `tools/claim.py` uses.

It also refuses, before it claims anything, a `--cycle <n>` whose
`-cycle-<n>.md` entry is already in the folder. That is a 1 rather than a
2: unlike a held sequence number there is no next one to bump to, and the
caller has a decision to make -- see `cycle_already_filed`.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Repo root on sys.path so `python3 tools/put_entry.py` works and not only
# `-m`. See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_claims import DONE, ClaimError, dumps, load, release, take

VAULT_TOOL = "/app/bridge/vault_tool.py"
JOURNAL_DIR = "projects/sokrates/projects/agora/nova/journal/"
CLAIMS_PATH = "projects/sokrates/projects/agora/nova/resources/claims.json"
OSLO = ZoneInfo("Europe/Oslo")

#: Same meaning as `tools/claim.py`'s: a refusal is the answer, not a fault.
REFUSED_EXIT = 2

#: How many numbers to walk before giving up. Three cycles can overlap at
#: 18 minutes and a fourth is already pathological, so this is not a
#: measured ceiling on contention -- it is a bound on a retry loop that
#: talks to the network, and it is stated rather than hidden.
DEFAULT_ATTEMPTS = 8

GRANTED, REFUSED, LOST = "granted", "refused", "lost"


class _Parser(argparse.ArgumentParser):
    """Argparse exits 2 on a usage error, and 2 means "no free number".

    Identical reasoning to `tools/claim.py`: 2 is an answer a caller acts
    on, so a mistyped flag must not be able to produce it.
    """

    def error(self, message):
        self.exit(1, f"{self.prog}: error: {message}\n")


def entry_name(seq, cycle, slug=None):
    """`370-cycle-343.md` -- zero padded so a lexical sort stays chronological.

    `slug` replaces the `cycle-<n>` tail for a run that is not one of the
    hourly cycles -- `421-monday-research.md`. A weekly heartbeat is a
    separate Agora conversation with its own counter starting at 1, so
    stamping its entry `cycle-1` would put it under a name Cycle 1 already
    used in 2026-08-01. It carries no cycle number instead, which is a
    shape the journal already has: the fifteen `NNN-report-...` documents
    predate this and render fine, `nova_journal.file_cycle` answers `None`
    for them, and `cycle_health.missing_cycles` counts hourly cycles off
    the `-cycle-` filenames and so cannot mistake a weekly entry for a gap.
    """
    if slug:
        return f"{seq:03d}-{slug}.md"
    return f"{seq:03d}-cycle-{cycle}.md"


#: A weekly slug names a file in the one folder that is the journal's total
#: order, so it is validated rather than trusted. `cycle-<anything>` is
#: refused specifically: `nova_journal.file_cycle` and the gap detector both
#: read `-cycle-(\d+)` out of a filename, and a weekly entry that smuggled
#: that substring in would be counted as an hourly cycle that never ran.
_WEEKLY_SLUG_RE = re.compile(r"\A(?!cycle-)[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def weekly_slug(value):
    """argparse type for `--weekly`: lowercase, hyphens, never `cycle-...`."""
    if not _WEEKLY_SLUG_RE.match(value or "") or "-cycle-" in value:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a weekly slug: lowercase letters, digits and "
            "single hyphens, and it may not contain `cycle-` -- that is how "
            "the journal names an hourly cycle and the gap detector reads it"
        )
    return value


def seq_slug(seq):
    """The claim slug for a sequence number.

    Padded the same way the filename is, so `journal-seq-070` and
    `journal-seq-70` can never be two names for one number.
    """
    return f"journal-seq-{seq:03d}"


def taken_seqs(names):
    """Every `<seq>` already present in the folder listing.

    A set rather than a maximum, because the maximum is what the old
    hand-run instruction used and it cannot see a hole. If 368 and 370
    exist and 369 does not, the next entry belongs at 371 -- 369 is a
    cycle that woke and wrote nothing, and `prompt.md` is explicit that a
    gap is a true fact about the record and is never repaired.
    """
    found = set()
    for name in names:
        stem = name.rsplit("/", 1)[-1]
        head = stem.split("-", 1)[0]
        if head.isdigit():
            found.add(int(head))
    return found


def next_seq(names):
    """The previous highest plus one, or 1 for an empty folder."""
    found = taken_seqs(names)
    return max(found) + 1 if found else 1


def cycle_already_filed(names, cycle):
    """Entries in the folder listing that already carry `-cycle-<cycle>.md`.

    The `<seq>` half of the filename is guarded by the claims ledger above.
    The `<n>` half was guarded by nothing, and on 2026-08-30 that produced
    `715-cycle-649.md` and `716-cycle-649.md` -- two different runs, both
    honestly stamped 649, because Agora holds a separate counter per
    conversation and the weekly architecture heartbeat is its own
    conversation. Neither entry is wrong on its own and neither is a
    mistake to repair; what is broken is the pair. `nova_journal.file_cycle`
    reads the `-cycle-<n>` token to decide which cycle an entry
    belongs to, and
    the owner's comments are keyed by that number, so a reply he leaves on one
    card can surface on the other.

    Matched on the whole `-cycle-<n>.md` tail rather than a substring, so
    cycle 64 does not collide with cycle 649.
    """
    tail = f"-cycle-{int(cycle)}.md"
    return sorted(
        name.rsplit("/", 1)[-1]
        for name in names
        if name.rsplit("/", 1)[-1].endswith(tail)
    )


def reserve_seq(cycle, existing, claim_once, start=None, attempts=DEFAULT_ATTEMPTS):
    """Walk upward until one sequence number is claimed, and return it.

    `claim_once(slug, cycle)` returns one of `GRANTED` / `REFUSED` /
    `LOST`. Refused means another live cycle holds that number, so bump.
    Lost means the compare-and-swap on the ledger failed because somebody
    claimed something *else* in between -- that says nothing about this
    number, so retry the same one.

    Returns `(seq, trail)`, trail being one line per attempt so the
    journal entry can say what actually happened rather than guessing.
    Returns `(None, trail)` if `attempts` is used up.
    """
    seq = next_seq(existing) if start is None else start
    trail = []
    for _ in range(attempts):
        if seq in taken_seqs(existing):
            trail.append(f"{seq:03d}: already a file in the folder, bumping")
            seq += 1
            continue
        outcome = claim_once(seq_slug(seq), cycle)
        if outcome == GRANTED:
            trail.append(f"{seq:03d}: claimed by cycle {cycle}")
            return seq, trail
        if outcome == REFUSED:
            trail.append(f"{seq:03d}: held by another cycle, bumping")
            seq += 1
            continue
        if outcome == LOST:
            trail.append(f"{seq:03d}: lost the ledger compare-and-swap, retrying")
            continue
        raise ValueError(f"claim_once returned {outcome!r}")
    return None, trail


def _private(workdir, name):
    """A scratch path this process alone owns.

    Two overlapping cycles can run in the same bridge pod, so they share
    `/tmp`. A fixed `put_entry.claims.rev` would have both of them writing
    one revision file, and the second read would hand the first one
    somebody else's revision to compare-and-swap against -- a guard
    reporting success while guarding the wrong document. `prompt.md`'s own
    claim block uses `$$` for exactly this and says why; this is the same
    thing in Python.
    """
    import os
    return f"{workdir}/put_entry.{os.getpid()}.{name}"


def _run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=180, **kwargs)


class Vault:
    """The three vault calls this tool makes, so the tests can replace them."""

    def __init__(self, tool=VAULT_TOOL):
        self.tool = tool

    def ls(self, prefix):
        done = _run([sys.executable, self.tool, "ls", prefix])
        if done.returncode != 0:
            raise RuntimeError(f"vault ls failed: {done.stderr.strip()}")
        return [line.strip() for line in done.stdout.splitlines() if line.strip()]

    def get(self, path, rev_file):
        """Text of `path`, or `None` when the vault says it is not there.

        `get` prints `[not found: <path>]` on stdout and exits 0, which is
        why the return code alone is not read here -- the same trap
        `backlog_brief._fetch` documents.
        """
        done = _run([sys.executable, self.tool, "get", path, "--rev-file", rev_file])
        if done.returncode != 0:
            raise RuntimeError(f"vault get {path} failed: {done.stderr.strip()}")
        if done.stdout.lstrip().startswith("[not found:"):
            return None
        return done.stdout

    def put(self, path, local, rev_file):
        """Returncode: 0 written, 3 lost the compare-and-swap, else failure."""
        done = _run([sys.executable, self.tool, "put", path, local,
                     "--if-rev-file", rev_file])
        if done.returncode not in (0, 3):
            raise RuntimeError(
                f"vault put {path} failed ({done.returncode}): "
                f"{(done.stderr or done.stdout).strip()}"
            )
        return done.returncode


def _ledger_pass(vault, workdir, decide):
    """One get / decide / put round trip against the shared ledger.

    Returns `GRANTED`, `REFUSED`, or `LOST`; `decide(ledger)` returns
    `(ok, message)` the way `nova_claims.take` and `.release` both do.
    """
    rev = _private(workdir, "claims.rev")
    local = _private(workdir, "claims.json")
    ledger = load(vault.get(CLAIMS_PATH, rev) or "")
    ok, message = decide(ledger)
    print(f"  {message}", file=sys.stderr)
    if not ok:
        return REFUSED
    with open(local, "w", encoding="utf-8") as handle:
        handle.write(dumps(ledger))
    return GRANTED if vault.put(CLAIMS_PATH, local, rev) == 0 else LOST


def vault_claim_once(vault, workdir, cycle_note=None):
    """A `claim_once` backed by the real ledger."""

    def claim_once(slug, cycle):
        return _ledger_pass(vault, workdir, lambda ledger: take(
            ledger, slug, cycle, datetime.now(OSLO), note=cycle_note))

    return claim_once


def release_seq(vault, workdir, seq, cycle, attempts=DEFAULT_ATTEMPTS):
    """Mark the number finished once the entry is written. Not optional.

    `nova_claims.prune` collects an abandoned `open` row after
    `DONE_KEEP_HOURS` as of runner#314, so this is no longer the difference
    between a permanent row and none. It is still the difference between a
    row that says what happened and a row that sits there for a day saying a
    cycle died -- about 80 a day at an 18-minute heartbeat, in the one
    document every claim in this loop reads and rewrites, and every one of
    them a sequence number that was in fact used. My first version of this
    tool did not call it, and the reviewer found it.

    A lost compare-and-swap here is retried rather than swallowed, because
    the failure is silent: the entry is already written and nothing later
    looks at the number again.
    """
    for _ in range(attempts):
        outcome = _ledger_pass(vault, workdir, lambda ledger: release(
            ledger, seq_slug(seq), cycle, datetime.now(OSLO), outcome="written",
            state=DONE))
        if outcome != LOST:
            return outcome
    return LOST


def lint(draft, name, repo_root):
    done = _run([sys.executable, "-m", "tools.lint_entry", draft, "--name", name],
                cwd=repo_root)
    sys.stderr.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode == 0


def main(argv=None):
    parser = _Parser(
        description="Reserve a journal sequence number and write the entry under it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("draft", help="the entry as written, on local disk")
    parser.add_argument("--cycle", type=int, required=True,
                        help="Agora cycle number, from agora_runner.cycle_number")
    parser.add_argument("--weekly", default=None, type=weekly_slug,
                        help="name a weekly run's entry `<seq>-<slug>.md` instead "
                             "of `<seq>-cycle-<n>.md`, e.g. --weekly monday-research")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument("--workdir", default="/tmp")
    parser.add_argument("--note", default=None, help="note recorded on the claim")
    args = parser.parse_args(argv)

    repo_root = str(_pathlib.Path(__file__).resolve().parents[1])
    vault = Vault()
    try:
        existing = vault.ls(JOURNAL_DIR)
    except RuntimeError as exc:
        print(f"put_entry: {exc}", file=sys.stderr)
        return 1
    if not args.weekly:
        clash = cycle_already_filed(existing, args.cycle)
        if clash:
            print(
                f"put_entry: cycle {args.cycle} is already filed as "
                f"{', '.join(clash)} -- refusing to write a second entry under "
                "that number. If this is a weekly run, pass "
                "`--weekly <slug>`: its heartbeat is a separate Agora "
                "conversation with its own counter, so its number is not the "
                "hourly loop's. If it is an hourly run, read the filed entry "
                "before writing anything -- two entries under one number "
                "misroute the owner's comments, which are keyed by it.",
                file=sys.stderr,
            )
            return 1
    try:
        seq, trail = reserve_seq(
            args.cycle, existing,
            vault_claim_once(vault, args.workdir, args.note),
            attempts=args.attempts,
        )
    except (ClaimError, RuntimeError) as exc:
        print(f"put_entry: {exc}", file=sys.stderr)
        return 1
    for line in trail:
        print(f"  {line}", file=sys.stderr)
    if seq is None:
        print(f"put_entry: no free sequence number in {args.attempts} attempts",
              file=sys.stderr)
        return REFUSED_EXIT

    name = entry_name(seq, args.cycle, args.weekly)
    if not lint(args.draft, name, repo_root):
        # The claim is deliberately left standing. `nova_claims.take`
        # grants a cycle its own open claim again, so fixing the entry and
        # re-running lands on the same number -- whereas releasing it marks
        # the slug *done*, and a done slug is refused even to the cycle
        # that owns it. Releasing here would make the retry bump.
        print("put_entry: lint refused the entry -- nothing was written",
              file=sys.stderr)
        return 1

    path = JOURNAL_DIR + name
    rev = _private(args.workdir, "entry.rev")
    try:
        if vault.get(path, rev) is not None:
            print(f"put_entry: {path} already exists -- refusing to overwrite",
                  file=sys.stderr)
            return 1
        code = vault.put(path, args.draft, rev)
    except RuntimeError as exc:
        print(f"put_entry: {exc}", file=sys.stderr)
        return 1
    if code == 3:
        print(f"put_entry: lost the write of {path} to another cycle", file=sys.stderr)
        return 1
    try:
        release_seq(vault, args.workdir, seq, args.cycle)
    except (ClaimError, RuntimeError) as exc:
        # The entry is written; a ledger row that stays open is untidy,
        # not lost work, so this must not turn a good write into a failure.
        print(f"put_entry: wrote the entry but could not release the claim: {exc}",
              file=sys.stderr)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
