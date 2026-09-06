"""Put a commit of SokratesAI/marcus-backup back into the running Marcus.

Issue #153 asked for a backend, cross-device persistence and a backup.
`platform-config#603` built the last of those: an hourly job commits Marcus's
whole state document to `SokratesAI/marcus-backup`, and I measured it working
end to end on 2026-09-07 -- the committed `marcus-state.json` and the live
`GET /api/state` carried byte-identical `data` at rev 3.

**The half that did not exist is the way back.** Nothing anywhere read that
repo into a running Marcus. The app's own "Restore from a file" button reads
the *export* envelope `buildBackup` writes in the browser; the repo holds the
*server* document (`{rev, updatedAt, data}`), a different shape, and no path
joined the two. A backup nobody has ever restored is a file, not a backup, so
this is the tool that turns 27 archived revisions into something recoverable.

**Why the backup's own `rev` is deliberately thrown away.** `StateStore.put`
refuses a write built on a stale revision, and the archived document carries
the revision it was taken at. Sending that back would be refused in exactly
the case you are restoring for -- the server has moved on since, which is
usually *why* you are here. The revision guards two phones racing each other;
it says nothing about an operator deliberately choosing an older copy. So the
restore reads the live revision and writes the archived `data` at it.

**Restore replaces, it does not merge**, which is the same call the in-app
restore made: merging a training log by hand is a guess, and a guess that
deletes a logged session is worse than an error. The confirmation is therefore
the per-store count printed below, not the word "restore" -- you check the
numbers you are about to write against the numbers you are about to lose.

**Nothing is written without `--apply`, and `--apply` pins a restore point
first.** The live document is saved to disk and the exact command to put it
back is printed before the write goes out, so choosing the wrong commit costs
one more command rather than a training history.

**Exit contract.** 0 when the comparison was printed (dry run) or the restore
landed. 1 when either side could not be read, or the write was refused -- a
Marcus that did not answer must never read as a Marcus with an empty state.
"""

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

# Repo root on sys.path so `python3 tools/marcus_restore.py` works and not
# only `-m`. See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

DEFAULT_URL = "http://marcus.agents.svc.cluster.local:8080/api/state"
DEFAULT_REPO = "SokratesAI/marcus-backup"
STATE_FILE = "marcus-state.json"
TIMEOUT = 30

#: The shared checkout directory, deliberately not `$NOVA_WORKSPACE`. A
#: concurrent cycle's workspace is a private worktree that is deleted when its
#: turn ends, and a restore point that dies with the cycle that made it is not
#: a restore point. `prompt.md`'s "make it reversible, then do it" names this
#: path for exactly that reason.
RESTORE_POINT_DIR = "/data/workspace"


class Unreadable(Exception):
    """One side could not be read. Never means "it was empty"."""


def _as_state(raw, source):
    """Parse bytes/str into a Marcus state document, or raise.

    `data` must be an object: `null` is what the store reports before anything
    has ever been written, and restoring that over a live Marcus would be a
    silent wipe dressed as a successful run. A proxy's HTML error page and a
    truncated body both land here rather than downstream.
    """
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as err:
        raise Unreadable(f"{source} is not JSON: {err}")
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), dict):
        keys = sorted(doc) if isinstance(doc, dict) else type(doc).__name__
        raise Unreadable(f"{source} is not a Marcus state document: {keys}")
    return doc


def read_live(url=DEFAULT_URL, opener=urllib.request.urlopen):
    """The running Marcus's state document, or raise.

    The revision has to be a whole number here even though `_as_state` does not
    require one: it is the value this tool sends back, and a document without
    one cannot be written on top of.
    """
    try:
        with opener(url, timeout=TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                raise Unreadable(f"{url} answered {response.status}")
            body = response.read()
    except (urllib.error.URLError, OSError) as err:
        raise Unreadable(f"{url} could not be reached: {err}")
    doc = _as_state(body, url)
    if not isinstance(doc.get("rev"), int) or isinstance(doc.get("rev"), bool):
        raise Unreadable(f"{url} carries no usable revision: {doc.get('rev')!r}")
    return doc


def read_backup(repo=DEFAULT_REPO, ref=None, path=None, run=subprocess.run):
    """The archived state document from a local file or the backup repo.

    `gh api` rather than a raw URL because the repo is private and `gh` already
    holds the credential every other tool here uses.
    """
    if path is not None:
        try:
            with open(path, "rb") as handle:
                return _as_state(handle.read(), path)
        except OSError as err:
            raise Unreadable(f"{path} could not be read: {err}")
    where = f"{repo}@{ref or 'HEAD'}:{STATE_FILE}"
    query = f"repos/{repo}/contents/{STATE_FILE}"
    if ref:
        query += f"?ref={ref}"
    result = run(["gh", "api", query, "--jq", ".content"],
                 capture_output=True, text=True)
    if result.returncode != 0:
        raise Unreadable(f"{where} could not be fetched: "
                         f"{(result.stderr or '').strip() or 'gh api failed'}")
    import base64
    try:
        raw = base64.b64decode(result.stdout)
    except (ValueError, TypeError) as err:
        raise Unreadable(f"{where} did not decode: {err}")
    return _as_state(raw, where)


def compare(live, backup):
    """Per-store `(key, live_count, backup_count)`, over the union of keys.

    The union, not the backup's keys: a store the backup does not carry is one
    the restore silently drops, and that is precisely the row an operator needs
    to see before saying yes.
    """
    live_data = live.get("data") or {}
    backup_data = backup.get("data") or {}
    rows = []
    for key in sorted(set(live_data) | set(backup_data)):
        rows.append((key, _count(live_data.get(key)), _count(backup_data.get(key))))
    return rows


def _count(value):
    """How many records a store holds, or None when it holds nothing."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return len(value)
    return 1


def _fmt(count):
    return "absent" if count is None else str(count)


def write_restore_point(live, directory, now=time.gmtime):
    """The live document on disk, and the command that puts it back.

    Rule 5's conversion: the restore is destructive and this is what makes it
    reversible, so it is written *before* the PUT rather than offered after.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", now())
    target = os.path.join(directory, f"marcus-restore-point-{stamp}.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(live, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return target


def put_state(rev, data, url=DEFAULT_URL, opener=urllib.request.urlopen):
    """Write `data` at `rev`. Returns the new document, or raises."""
    body = json.dumps({"rev": rev, "data": data}).encode()
    request = urllib.request.Request(
        url, data=body, method="PUT",
        headers={"Content-Type": "application/json"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            return _as_state(response.read(), url)
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = json.loads(err.read()).get("error", "")
        except Exception:  # noqa: BLE001 - the status is the finding, not this
            pass
        raise Unreadable(f"{url} refused the write with {err.code}: "
                         f"{detail or 'no reason given'}")
    except (urllib.error.URLError, OSError) as err:
        raise Unreadable(f"{url} could not be written: {err}")


def report(live, backup, rows, apply_, out=print, source="", url=DEFAULT_URL):
    """The comparison block. Reads nothing and writes nothing."""
    out(f"live     rev {live.get('rev')} at {live.get('updatedAt') or '(never)'}  {url}")
    out(f"backup   rev {backup.get('rev')} at {backup.get('updatedAt') or '(unknown)'}  {source}")
    out("")
    out("store           live -> restored")
    for key, live_count, backup_count in rows:
        marker = "" if live_count == backup_count else "   <- changes"
        out(f"  {key:<12} {_fmt(live_count):>4} -> {_fmt(backup_count):<6}{marker}")
    if not rows:
        out("  (no stores on either side)")
    out("")
    if not apply_:
        out("Nothing was written. Re-run with --apply to restore, which replaces "
            "the live state with the backup rather than merging it.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL,
                        help="Marcus's state endpoint (default: %(default)s)")
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help="backup repository (default: %(default)s)")
    parser.add_argument("--ref", default=None,
                        help="commit, tag or branch in the backup repo "
                             "(default: the repo's default branch)")
    parser.add_argument("--file", default=None,
                        help="a local state document, instead of the repo")
    parser.add_argument("--apply", action="store_true",
                        help="actually write it; without this nothing is sent")
    parser.add_argument("--restore-point-dir", default=RESTORE_POINT_DIR,
                        help="where --apply saves the current state first "
                             "(default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        backup = read_backup(repo=args.repo, ref=args.ref, path=args.file)
        live = read_live(args.url)
    except Unreadable as err:
        print(f"CANNOT SEE  {err}")
        print("            Nothing was written. This is not a report that "
              "Marcus is empty.")
        return 1

    source = args.file or f"{args.repo}@{args.ref or 'HEAD'}"
    rows = compare(live, backup)
    report(live, backup, rows, args.apply, source=source, url=args.url)
    if not args.apply:
        return 0

    try:
        pinned = write_restore_point(live, args.restore_point_dir)
    except OSError as err:
        print(f"CANNOT SEE  the restore point could not be written: {err}")
        print("            Nothing was sent to Marcus. Pass "
              "--restore-point-dir somewhere writable.")
        return 1
    print(f"restore point: {pinned}")
    print(f"  put it back with: python3 -m tools.marcus_restore "
          f"--file {pinned} --apply")

    try:
        written = put_state(live["rev"], backup["data"], args.url)
    except Unreadable as err:
        print(f"REFUSED  {err}")
        print("         A 409 means Marcus moved on between the read and the "
              "write -- run it again. Nothing was changed.")
        return 1
    print(f"restored: rev {live['rev']} -> {written.get('rev')} at "
          f"{written.get('updatedAt')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
