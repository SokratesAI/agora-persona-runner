"""Scan the local checkouts for committed secrets, and prove the scanner works first.

Cycle 411. `platform-config`'s only CI check is a Secret scan, and it has
failed in 2-4 seconds on every run since 2026-08-23 -- a GitHub billing
problem the owner has parked until 2026-09-01. The handoff's answer was "run
`gitleaks` by hand in the meantime", which is a reasonable answer and was
never a reliable one, because nothing said where `gitleaks` is.

**The failure this tool exists to stop is the check, not the leak.**
Cycle 403 ran `gitleaks` successfully on 2026-08-25 morning. Cycle 409 ran
`command -v gitleaks`, got nothing, and wrote "not installed in the bridge
pod or the runner pod" into the handoff and into the backlog as a measured
fact. Both cycles were looking at the same box. The binary was at
`/tmp/gitleaks` the whole time -- downloaded by Cycle 403, still executable,
version 8.30.1 -- and `command -v` searches `PATH`, which `/tmp` is not on.
So the negative result was guaranteed in advance by the instrument, which is
the failure `prompt.md` spends four paragraphs on, and the cost of it was
two cycles believing a security check was impossible while it sat one
absolute path away.

`/tmp` is also genuinely ephemeral: the bridge pod restarts and the binary
goes. So "is it there" has two different right answers on two different
days, which is exactly the kind of question a cycle should not be answering
by hand at all.

    python3 -m tools.secret_scan                 # every checkout in the workspace
    python3 -m tools.secret_scan --repo <path>   # one of them

**The canary is the point of this file.** Before any repo is reported
clean, the scanner is pointed at a directory this module writes containing
two synthetic credentials, and it must find both. A `gitleaks` that scanned
nothing -- wrong path, unreadable tree, a `.gitleaksignore` that swallows
everything, a binary that runs and exits 0 without matching -- prints
`no leaks found` and exits 0, which is byte-for-byte what a genuinely clean
repo prints. That is a positive result guaranteed in advance, and this loop
has shipped three of them (`lint_entry | tail`, `set -e` in the bridge
shell, the reviewer experiment's own counter). So a clean report here is
never a bare exit 0 from the scanner; it is a scanner that was watched
catching something first.

The canary strings are checked in deliberately. They are syntactically
valid and semantically dead -- a random AWS key id and a random GitHub PAT
shape, neither issued by anyone -- and they live in a Python string that is
written to a temporary directory at runtime rather than to a file in this
repo, so the repo this module ships in does not itself trip the scan it
runs. AWS's own documented example key (`AKIAIOSFODNN7EXAMPLE`) is
*allowlisted* by gitleaks and was the first canary tried; it detected
nothing and would have shipped as a proof that could never fail. Measured,
not assumed: that is why the canary asserts on rule ids rather than on a
count.

Exit status: 0 when the canary fired and every repo scanned clean, 2 when
there is a finding, 1 when no working scanner could be established or a
repo could not be read. "I could not check" never reads as "nothing here".
"""

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request

from tools import tidy_workspace

# Every subprocess call is bounded, the same way `tidy_workspace` bounds
# its git and gh calls. A hung scanner is the one failure that produces no
# output at all, which is worse than either answer it could have given.
SCAN_TIMEOUT_SECONDS = 300
GIT_TIMEOUT_SECONDS = 30

# Where a previous cycle's download is likely to be, in the order worth
# trying. `PATH` is consulted first and separately; these are the paths a
# `command -v` cannot see, which is the whole reason this list exists.
CANDIDATE_PATHS = (
    "/tmp/gitleaks",
    "/usr/local/bin/gitleaks",
    os.path.expanduser("~/.local/bin/gitleaks"),
)

DOWNLOAD_URL = (
    "https://github.com/gitleaks/gitleaks/releases/download/"
    "v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz"
)

# Two dead credentials with live *shapes*. The rule ids are asserted, not
# just the count: a scanner that matched one of them twice would satisfy a
# count of two while proving half as much.
# The `+` splits are load-bearing in both directions and are the reason this
# assignment looks awkward. Each canary must reach the written file as one
# contiguous run of characters or the scanner will not match it -- the first
# version of this constant split the key across two *string literals inside
# the written text*, so the temporary file said `"AKIA" "QYLP..."` and the
# canary silently failed. It must equally not appear contiguously in this
# module's own source, because this repo is one of the checkouts scanned
# below and a tool that flags itself is a tool nobody runs twice. A quote
# between the halves here satisfies both: the concatenation happens at
# import, the bytes on this line never form a key.
CANARY_FILE = "canary_do_not_commit.py"
CANARY_BODY = (
    'aws = "AKIA' + 'QYLPMN5HXFTZ3B7C"\n'
    'pat = "ghp_' + '1a2B3c4D5e6F7g8H9i0JkLmNoPqRsTuVwXyZ"\n'
)
CANARY_RULES = frozenset({"aws-access-token", "github-pat"})


def find_scanner(allow_download=True):
    """An absolute path to a runnable `gitleaks`, or None.

    Returns `(path, how)` so the caller can print where it came from --
    "found on PATH" and "downloaded just now" are different facts about
    the box and a cycle reading the output wants to know which it got.
    """
    on_path = shutil.which("gitleaks")
    if on_path:
        return on_path, "on PATH"
    for candidate in CANDIDATE_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate, "already on disk"
    if not allow_download:
        return None, "not on disk and --no-download was given"
    try:
        target = _download(CANDIDATE_PATHS[0])
    except Exception as exc:  # noqa: BLE001 -- the reason is printed, not raised
        return None, "download failed: %s" % exc
    return target, "downloaded just now"


def _download(target):
    """Fetch the release tarball and leave the binary at `target`."""
    with tempfile.TemporaryDirectory() as work:
        archive = os.path.join(work, "gitleaks.tar.gz")
        with urllib.request.urlopen(DOWNLOAD_URL, timeout=120) as response:
            with open(archive, "wb") as handle:
                shutil.copyfileobj(response, handle)
        subprocess.run(
            ["tar", "-xzf", archive, "-C", work, "gitleaks"],
            check=True,
            capture_output=True,
            timeout=SCAN_TIMEOUT_SECONDS,
        )
        shutil.move(os.path.join(work, "gitleaks"), target)
    os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR)
    return target


def scan_dir(scanner, path):
    """Run the scanner over `path`. Returns the list of findings.

    Raises `RuntimeError` if the scanner could not produce a report --
    which is deliberately not the same answer as an empty list.
    """
    with tempfile.TemporaryDirectory() as work:
        report = os.path.join(work, "report.json")
        result = subprocess.run(
            [
                scanner,
                "dir",
                path,
                "--no-banner",
                "--report-format",
                "json",
                "--report-path",
                report,
                # The report is a temporary file and the findings are
                # printed, so without this a live credential is written to
                # disk and then to stdout -- and stdout here gets pasted
                # into handoffs. A tool for stopping secrets spreading must
                # not be the thing that spreads one. Rule ids and locations
                # survive redaction, and those are the whole of what a
                # reader needs to go and look.
                "--redact",
            ],
            timeout=SCAN_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
        # gitleaks exits 1 when it finds something, 0 when it does not, and
        # something else when it could not run. Only the last is an error
        # here, and it is told apart by whether a report was written.
        if not os.path.exists(report):
            raise RuntimeError(
                (result.stderr or result.stdout or "no output").strip()[:400]
            )
        try:
            with open(report, encoding="utf-8") as handle:
                return json.load(handle) or []
        except (OSError, ValueError) as exc:
            # A truncated or unparseable report is exactly as much of a
            # non-answer as no report at all, and it must not arrive as a
            # traceback -- the docstring promises that "I could not check"
            # never reads as "nothing here", and a stack trace reads as
            # neither.
            raise RuntimeError("report was not readable JSON: %s" % exc)


def prove_scanner(scanner):
    """Point the scanner at two planted secrets. Returns the rules it found.

    The caller treats anything short of `CANARY_RULES` as no instrument.
    Nothing about a repo is reported until this has come back whole.
    """
    with tempfile.TemporaryDirectory() as work:
        with open(os.path.join(work, CANARY_FILE), "w", encoding="utf-8") as handle:
            handle.write(CANARY_BODY)
        return {finding.get("RuleID") for finding in scan_dir(scanner, work)}


def workspace_repos(workspace=None):
    """Every git checkout worth scanning, across every workspace root.

    Both halves of this are borrowed rather than re-derived, and the
    borrowing is the point. `tidy_workspace.workspace_roots` knows that a
    concurrent cycle has **two** roots -- its own private worktree and the
    shared `/data/workspace` -- and the first version of this function
    walked only `NOVA_WORKSPACE`, so it would have scanned four checkouts,
    printed "clean", and said nothing about the dozen it never opened.
    `tidy_workspace.clones` knows that `.git` is a file in a linked
    worktree *and* that a `_review-*` directory is a reviewer's scratch
    copy rather than a repo. I had rediscovered the first of those and not
    the second, which is what re-deriving a predicate looks like from the
    inside: the half that bit me today, and none of the halves that bit
    somebody else.

    `workspace` overrides the roots when given, for `--workspace`.
    """
    roots = [workspace] if workspace else tidy_workspace.workspace_roots()
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in tidy_workspace.clones(root):
            path = os.path.join(root, name)
            if path not in found:
                found.append(path)
    return found


def git_ignored(repo, paths):
    """The subset of `paths` that this repo's own `.gitignore` excludes.

    A finding in an ignored file is a finding in something that cannot be
    committed, so it is not a leak in the repo -- the first run of this tool
    reported its own `__pycache__/secret_scan.pyc`, because the canary
    constant a few lines up compiles into the bytecode verbatim. Asking git
    is the only correct way to answer this: a hardcoded `__pycache__` skip
    would have fixed today's noise and none of tomorrow's.
    """
    if not paths:
        return set()
    result = subprocess.run(
        ["git", "-C", repo, "check-ignore", "--stdin"],
        input="\n".join(paths),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    # Exit 0 = some ignored, 1 = none ignored, 128 = not a repo or git is
    # unhappy. Only the first two are answers; on anything else nothing is
    # filtered, which leaves the findings noisy rather than quietly gone.
    if result.returncode not in (0, 1):
        return set()
    return {line for line in result.stdout.splitlines() if line}


def sealed_secret_files(paths):
    """The subset of `paths` that are SealedSecret manifests.

    The 17 findings this filter removes are the whole point of the
    sealed-secrets pattern: the `AgA…` blobs are ciphertext that only the
    cluster's controller can open, and committing them to a public repo is
    what the tool is *for*. Reporting them as leaks would make every
    `platform-config` scan print seventeen findings that are correct to
    ignore, and a report that is 17/19 noise is one nobody reads twice --
    which is the same failure as no scan at all, arrived at from the other
    side.

    Deliberately narrow: the match is on the manifest's own `kind`, not on
    the path or the filename, so a plaintext Secret dropped into
    `secrets/sealed/` is still reported.
    """
    sealed = set()
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                head = handle.read(4096)
        except OSError:
            continue
        if "kind: SealedSecret" in head:
            sealed.add(path)
    return sealed


def filter_findings(repo, findings):
    """Findings worth a human's attention, and the count set aside.

    Returns `(kept, skipped)`. Every exclusion here is a *positive*
    identification -- git says the file is ignored, or the manifest says it
    is a SealedSecret. Nothing is dropped for being merely unlikely.
    """
    paths = [finding.get("File") or "" for finding in findings]
    excused = git_ignored(repo, [p for p in paths if p]) | sealed_secret_files(
        [p for p in paths if p]
    )
    kept = [f for f in findings if (f.get("File") or "") not in excused]
    return kept, len(findings) - len(kept)


def _label(repo):
    """`<workspace>/<repo>` -- enough to tell two checkouts of one repo apart."""
    parent, name = os.path.split(repo.rstrip("/"))
    return os.path.join(os.path.basename(parent), name)


def _describe(finding):
    where = finding.get("File") or "?"
    line = finding.get("StartLine")
    if line:
        where = "%s:%s" % (where, line)
    return "%s  %s  %s" % (
        (finding.get("RuleID") or "?").ljust(24),
        where,
        (finding.get("Secret") or "")[:12] + "…",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="a checkout to scan; repeatable. Defaults to every one in the workspace.",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="one root to look in; defaults to every workspace root tidy_workspace knows",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="fail rather than fetch gitleaks when it is not already on the box",
    )
    args = parser.parse_args(argv)

    scanner, how = find_scanner(allow_download=not args.no_download)
    if scanner is None:
        print("NO INSTRUMENT — could not get a scanner: %s" % how)
        print("  This is not 'no secrets found'. Nothing was scanned.")
        return 1
    print("scanner: %s (%s)" % (scanner, how))

    try:
        fired = prove_scanner(scanner)
    except RuntimeError as exc:
        print("NO INSTRUMENT — the scanner could not run: %s" % exc)
        return 1
    missing = CANARY_RULES - fired
    if missing:
        print(
            "NO INSTRUMENT — the canary was not detected (missing %s)."
            % ", ".join(sorted(missing))
        )
        print("  A clean result from this scanner would prove nothing, so none is given.")
        return 1
    print("canary: both planted credentials detected — the scanner can fail")

    repos = args.repo or workspace_repos(args.workspace)
    roots = [args.workspace] if args.workspace else tidy_workspace.workspace_roots()
    if not repos:
        # Deliberately not "NO INSTRUMENT": the scanner was established and
        # proved a moment ago. Reusing that phrase would make a grep for
        # scanner trouble return a case where the scanner was fine and the
        # path was wrong, which are different problems with different fixes.
        print("NOTHING SCANNED — no checkouts found under %s" % ", ".join(roots))
        return 1

    status = 0
    clean = []
    for repo in repos:
        try:
            findings = scan_dir(scanner, repo)
        except RuntimeError as exc:
            print("UNREADABLE  %s: %s" % (repo, exc))
            status = max(status, 1)
            continue
        findings, skipped = filter_findings(repo, findings)
        note = " (%d excused: git-ignored or SealedSecret)" % skipped if skipped else ""
        if findings:
            print("LEAK  %s — %d finding(s)%s" % (repo, len(findings), note))
            for finding in findings:
                print("    " + _describe(finding))
            status = 2
        else:
            # The basename alone is ambiguous now that both workspace roots
            # are swept: the same four repo names appear twice, and a reader
            # counting eight entries and four names cannot tell a duplicate
            # from a second checkout.
            clean.append(_label(repo) + note)
    if clean:
        print("clean: %s" % ", ".join(clean))
    if status == 0:
        print("Nothing to act on. Every checkout scanned with a proven scanner.")
    return status


if __name__ == "__main__":
    sys.exit(main())
