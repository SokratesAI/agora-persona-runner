"""Do the three persona-memory stores hold anything yet?

Idea #165 asked whether Agora could remember across conversations the
way the owner's own Claude sessions do. Cycle 688's answer was that the
feature was not missing -- three stores existed, were wired into the
system prompt, and were all empty. Cycle 689 shipped slice 1: the runner
sends `persona_id` and the bridge pins a per-persona auto-memory
directory, so a chat turn finally has somewhere to write. It left one
sentence open, and that sentence is why this file exists: *"the first
thing to measure is whether personas write to the file store unprompted
now that they have one."*

Measuring it by hand costs six calls -- list the directory on the bridge
PVC, stat each one, read the index, then join to Agora for the names,
because a directory is named by a persona id and an id is not a persona.
Cycle 1049 paid that and this is the same walk in one call, so the next
cycle to ask (and there will be one, because slices 2 and 3 are still
open) reads an answer instead of re-deriving the method.

The three stores, and what each one is:

- **File memory** -- `CLAUDE_HOME/persona-memory/<persona id>/`, one
  directory per persona, on the bridge pod's PVC. The CLI's own
  `autoMemoryDirectory` setting brings the instruction with it, which is
  the part worth knowing: a persona given a directory is *told* what a
  memory is for without anything in this repo saying so.
- **Persona `sharedMemory`** -- a text field on the persona record in
  Agora. Nothing writes it.
- **Conversation `memory`** -- a text field on the conversation record.
  Nothing writes it.

**This does not run in preflight and should not.** It answers a question
a cycle asks when it picks up idea #165, not one that needs asking every
hour; a check that says the same thing every morning is the cost
`preflight` was built to cut.

The exit contract is the one thing here that is a judgement rather than a
report. **Exit 2 means all three stores are empty**, which is the state
the idea was filed about -- if it ever says that again, slice 1 has
regressed and the two slices built on top of it are building on nothing.
Exit 1 is "no instrument": the personas list was unreadable, or the
memory root does not exist, and neither of those is a measurement of an
empty store. Exit 0 is any state where something, somewhere, is held.
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.heartbeat_liveness import AGORA_PUBLIC  # noqa: E402

OSLO = ZoneInfo("Europe/Oslo")

# The bridge pod's own layout (bridge/quota.py: CLAUDE_HOME +
# "persona-memory"). Read as a default rather than hardcoded so a test can
# point at a directory it built, and so this still answers if the mount
# ever moves.
DEFAULT_ROOT = os.path.join(
    os.environ.get("CLAUDE_HOME", "/data/claude-home"), "persona-memory")

# The CLI writes its index here. A directory holding memories but no index
# is worth naming rather than counting as fine: recall reads the index.
INDEX = "MEMORY.md"


def _oslo(epoch):
    """`2026-09-04 09:10 Oslo`, per rule 7 -- he does not read UTC."""
    return datetime.fromtimestamp(epoch, OSLO).strftime("%Y-%m-%d %H:%M Oslo")


def read_dirs(root):
    """One row per persona directory under `root`, or `None` if it is absent.

    `None` rather than `[]`, because "the mount is not here" and "no
    persona has ever been given a directory" are the two states this must
    not confuse -- the first is a broken instrument and the second is the
    finding.
    """
    if not os.path.isdir(root):
        return None
    rows = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        files = sorted(f for f in os.listdir(path)
                       if os.path.isfile(os.path.join(path, f)))
        stamps = [os.path.getmtime(os.path.join(path, f)) for f in files]
        rows.append({
            "persona_id": name,
            "files": len(files),
            "has_index": INDEX in files,
            "newest": max(stamps) if stamps else None,
            "created": os.path.getmtime(path),
        })
    return rows


def _get(path, opener=None, timeout=60):
    """`(payload, error)` for one Agora GET. Never raises."""
    target = AGORA_PUBLIC.rstrip("/") + path
    try:
        with (opener or urllib.request.urlopen)(target, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), None
    except Exception as e:
        return None, f"could not read {target}: {e}"


def _rows(payload, key):
    if isinstance(payload, dict):
        payload = payload.get(key, [])
    if not isinstance(payload, list):
        return []
    return [r for r in payload if isinstance(r, dict)]


def _filled(rows, field):
    """How many rows carry a non-blank `field`. Whitespace is not content."""
    return sum(1 for r in rows if (r.get(field) or "").strip())


def judge(dirs, personas, conversations, root=DEFAULT_ROOT, error=None):
    """`(text, status)` --- the whole report and its exit code."""
    lines = []
    if error:
        lines.append(f"COULD NOT READ — {error}")
        lines.append("This is no instrument, not three empty stores.")
        return "\n".join(lines), 1
    if dirs is None:
        lines.append(f"COULD NOT READ — no directory at {root}. On the bridge "
                     "pod this is the PVC mount; from anywhere else it is "
                     "absent because the store lives there and nowhere else.")
        lines.append("This is no instrument, not three empty stores.")
        return "\n".join(lines), 1

    names = {p.get("id"): (p.get("name") or "?") for p in personas}
    held = [d for d in dirs if d["files"]]
    shared = _filled(personas, "sharedMemory")
    notes = _filled(conversations, "memory")

    if held:
        lines.append(f"FILE MEMORY — {len(held)} of {len(dirs)} persona "
                     f"director{'y' if len(dirs) == 1 else 'ies'} hold "
                     "memories written by the persona itself.")
    else:
        lines.append(f"FILE MEMORY — EMPTY. {len(dirs)} director"
                     f"{'y' if len(dirs) == 1 else 'ies'} exist and none of "
                     "them holds a file.")
    for d in dirs:
        who = f"{names.get(d['persona_id'], '?')} ({d['persona_id']})"
        if d["files"]:
            index = "index present" if d["has_index"] else "NO MEMORY.md index"
            lines.append(f"  {who}: {d['files']} file(s), {index}, newest "
                         f"{_oslo(d['newest'])}")
        else:
            lines.append(f"  {who}: empty since the directory was made, "
                         f"{_oslo(d['created'])}")

    for label, filled, total, of in (
            ("persona sharedMemory", shared, len(personas), "persona(s)"),
            ("conversation memory", notes, len(conversations),
             "conversation(s)")):
        verdict = "EMPTY" if not filled else f"{filled} of {total} carry one"
        lines.append(f"{label}: {verdict} — {total} {of} read.")

    lines.append(f"Read from {root} and {AGORA_PUBLIC}. Nova's own cycle "
                 "memory is not under this root — the bridge points the "
                 "cycle persona at CLAUDE_HOME/nova-memory instead, so its "
                 "absence here is correct and not a gap.")

    if not held and not shared and not notes:
        lines.append("All three stores are empty. That is the state idea #165 "
                     "was filed about, so slice 1 has regressed rather than "
                     "never having worked.")
        return "\n".join(lines), 2
    return "\n".join(lines), 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=DEFAULT_ROOT,
                    help="persona-memory directory (default: the PVC mount)")
    args = ap.parse_args(argv)

    dirs = read_dirs(args.root)
    personas, error = _get("/personas")
    conversations = []
    if error is None:
        conversations, error = _get("/conversations")
    text, status = judge(dirs,
                         _rows(personas, "personas"),
                         _rows(conversations, "conversations"),
                         root=args.root, error=error)
    print(text)
    return status


if __name__ == "__main__":
    sys.exit(main())
