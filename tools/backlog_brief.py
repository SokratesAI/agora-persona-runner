"""My own two capture files, as a brief small enough to read every cycle.

Edvard, capture on `ideas.md` 2026-08-23: *"Cut per-cycle token cost of
loading resources/issues.md and other stable/large files in full every
cycle — the dreaming-pass cleanup helps but doesn't address the read cost
itself."*

He is naming a cost I pay in the opening read and have never measured.
`prompt.md` step 1b hands the subagent `nova/resources/issues.md` and
`nova/resources/ideas.md` with the instruction "condensed" -- but
condensing happens *after* the bytes are in a context window, so the word
saves nothing on the way in. Measured 2026-08-23 against the live files:
**246,737 and 79,747 bytes, 712 notes between them.** Of that, 313KB is
the `## Entries` history and 9.8KB is `## Retired`, which is retired
precisely because no cycle needs it.

What a cycle actually uses out of those files is the recent end. A note
from Cycle 24 is not a backlog item, it is an archaeological record --
it stays in the file because the file is the permanent log, and that is
the right thing for the file to be and the wrong thing to read in full
sixteen times a day.

So this prints three things per file and nothing else:

- **the head section verbatim** -- the bullets above `## Entries`, which
  are current friction rather than history, and are 2.9KB for both files
  together. Cheap, and the part most likely to matter.
- **the newest `--limit` entries**, default 40.
- **one line naming exactly what was dropped**: how many entries, and the
  date of the newest one that did not make the cut. A brief that hides its
  own truncation is the thing `personality.md` calls a silent permanent
  decision; a brief that names the boundary lets the next cycle widen it
  with `--limit` when it is genuinely chasing something old.

`## Retired` is dropped entirely and counted, for the same reason.

**Ordering is computed, not assumed, and that is the one real trap here.**
`parse_notes`'s own docstring records that these files carried two append
conventions at once -- newest-first at the top, oldest-first at the bottom,
because `vault_tool.py append` inserts under an `## Entries` marker when
handed one and at the end of the file when not. `normalise_captures.py`
merged the two streams and the live files read newest-first today
(measured: both start 2026-08-23 and end 2026-08-12/08-11). Taking a slice
off the top would therefore work right now and break silently the next time
anything appends the old way, returning the *oldest* forty entries with no
symptom. So the sort is on the `(date, cycle)` marker the notes carry --
489 of 526 and 158 of 186 have a date, 512 and 167 have a cycle number.

Undated entries sort last and are **counted separately in the dropped
line** rather than folded into it: "37 undated" is a fact about my own
file-writing discipline that a cycle should see, not a rounding error.

Vault I/O is inside the tool, like `top_board_rows`, for the same reason:
an opening read that takes three commands is one a cycle will skip.
`--issues`/`--ideas` take local files instead, which is how the tests drive
it and how the runner pod (no vault client) can use it at all.
"""

import argparse
import subprocess
import sys

# Repo root on sys.path so `python3 tools/backlog_brief.py` works and not
# only `-m`. See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import BOARD_PATHS, _sections, parse_notes

VAULT_TOOL = "/app/bridge/vault_tool.py"

# Taken from `BOARD_PATHS` rather than typed again, exactly as
# `top_board_rows` does: these are my own two capture files, the `"nova"`
# half of the same mapping whose `"edvard"` half that tool reads. A second
# hand-typed copy of a path is a copy that will be wrong the next time the
# folder moves, and wrong here is quiet -- an unresolvable path reads as a
# file with no notes, which is indistinguishable from a clean backlog.
ISSUES_PATH = BOARD_PATHS["issues"]["nova"]
IDEAS_PATH = BOARD_PATHS["ideas"]["nova"]

DEFAULT_LIMIT = 40


def _fetch(path):
    """`vault_tool.py get` as text, or `None` if it did not really return one.

    Same shape as `top_board_rows._fetch`, and for the same measured
    reason: `vault_tool.py get` prints `[not found: <path>]` on stdout and
    exits **0**, so a return code alone reads a vanished file as an empty
    one. Here that would print a brief saying I have no open notes, which
    is the most reassuring possible way to be wrong.
    """
    try:
        done = subprocess.run([sys.executable, VAULT_TOOL, "get", path],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    if not done.stdout.strip() or done.stdout.lstrip().startswith("[not found:"):
        return None
    return done.stdout


def _sort_key(note):
    """Newest first, on the marker the note carries rather than its position.

    Returned for use with `reverse=True`, so an undated note has to sort
    *below* every dated one -- which means its key must be smaller than any
    real date, not larger. `""` is smaller than any `"2026-.."` string, and
    `-1` smaller than any cycle number, so the empty marker falls to the
    bottom on its own without a second pass.
    """
    return (note.get("date") or "", note.get("cycle") or -1)


def head_section(markdown):
    """The bullets above `## Entries` -- current friction, not history.

    Returned as raw lines rather than parsed notes because this section is
    not uniform: in `resources/issues.md` it carries three paragraphs of
    convention text alongside the bullets, and re-flowing that would be me
    editing a file I was asked to read more cheaply.
    """
    for _, title, body in _sections(markdown):
        if title.strip().lower() not in ("entries", "retired"):
            return body.strip("\n")
    return ""


def retired_count(markdown):
    """How many notes sit under `## Retired`, which the brief never prints."""
    for _, title, body in _sections(markdown):
        if title.strip().lower() == "retired":
            return sum(1 for line in body.split("\n")
                       if line.strip().startswith("- ") and line.strip()[2:].strip())
    return 0


def brief(markdown, limit=DEFAULT_LIMIT):
    """One capture file -> `{head, kept, dropped, undated, oldest_kept,
    newest_dropped, retired}`.

    `dropped` counts every entry the brief does not print, so
    `len(kept) + dropped` is the whole `## Entries` section and the caller
    never has to work out whether the number it was given was before or
    after truncation.
    """
    notes = parse_notes(markdown)
    ordered = sorted(notes, key=_sort_key, reverse=True)
    kept = ordered[:limit] if limit > 0 else ordered
    dropped = ordered[limit:] if limit > 0 else []
    return {
        "head": head_section(markdown),
        "kept": kept,
        "dropped": len(dropped),
        "undated": sum(1 for n in dropped if not n.get("date")),
        "oldest_kept": next((n["date"] for n in reversed(kept) if n.get("date")), ""),
        "newest_dropped": next((n["date"] for n in dropped if n.get("date")), ""),
        "retired": retired_count(markdown),
        "total": len(notes),
        "bytes": len(markdown),
    }


def render(label, data, limit):
    """One file's brief as the text a cycle reads."""
    out = [f"== {label} — {data['total']} entries, {data['bytes']:,} bytes in the vault"]
    if data["head"]:
        out.append(data["head"])
        out.append("")
    out.append(f"-- newest {len(data['kept'])} of {data['total']} entries --")
    for note in data["kept"]:
        stamp = note.get("date") or "(undated)"
        cycle = f" (Cycle {note['cycle']})" if note.get("cycle") else ""
        out.append(f"- {stamp}{cycle} — {note['text']}")
    tail = []
    if data["dropped"]:
        edge = f", newest dropped dated {data['newest_dropped']}" if data["newest_dropped"] else ""
        undated = f", {data['undated']} of them undated" if data["undated"] else ""
        tail.append(f"NOT SHOWN: {data['dropped']} older entries{undated}{edge}. "
                    f"Re-run with --limit {limit * 2} or --limit 0 for all of them.")
    if data["retired"]:
        tail.append(f"NOT SHOWN: {data['retired']} entries under ## Retired.")
    if tail:
        out.append("")
        out.extend(tail)
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="newest N entries per file; 0 for all "
                             f"(default {DEFAULT_LIMIT})")
    parser.add_argument("--issues", help="read a local file instead of the vault")
    parser.add_argument("--ideas", help="read a local file instead of the vault")
    args = parser.parse_args(argv)

    failed = []
    blocks = []
    for label, path, override in (("resources/issues.md", ISSUES_PATH, args.issues),
                                  ("resources/ideas.md", IDEAS_PATH, args.ideas)):
        if override:
            markdown = _pathlib.Path(override).read_text()
        else:
            markdown = _fetch(path)
        if markdown is None:
            failed.append(path)
            continue
        blocks.append(render(label, brief(markdown, args.limit), args.limit))

    print("\n\n".join(blocks))
    if failed:
        # Loud, and non-zero, for the reason `_fetch` documents: the failure
        # mode of a quiet miss here is a brief that reads as "nothing open".
        print("\nCOULD NOT READ: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
