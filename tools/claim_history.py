"""Keep every cycle's claim and release for good, on every sweep.

Idea #312, under issue #227: the Nova key result "Every cycle shows up" is
the share of cycles in the last three months that show up in a planned vs.
done view, one line per cycle. The done half exists -- every cycle writes a
journal entry. The planned half is the claims ledger, which records what a
cycle said it would take (`note`) and what came of it (`state`, `outcome`),
and **the ledger prunes itself**: `nova_claims.prune` drops a released row
after 24 hours. Measured 2026-09-17 04:15 Oslo: 95 rows, the oldest from
09-16 04:56. So the plan half of three months was about one day long, and
every day before this ran is gone for good.

This copies the ledger into an append-only history document that nothing
prunes. It runs in `tools.preflight` on every sweep, the same shape as
`tools.host_cpu_ingest`: the source keeps a day and the sweep runs every
cycle, so a sweep that fails loses nothing as long as the next one inside a
day succeeds.

One row per `(item, cycle)`. A release rewrites the ledger row in place
(`open` -> `done`/`progressed`, with an `outcome`), so a later sweep updates
the history row rather than adding a second one, and `first_seen_at` keeps the
`at` of the first copy a sweep saw -- the take time when a sweep caught the
row open, the release time when the cycle took and released it between two
sweeps. `journal-seq-*` rows are left out: they are
`tools.put_entry` reserving a file number, not a plan, and they would be
half of the document.

Exit contract, the same one as its siblings in `tools.preflight`:

- 0 -- the history holds every row the ledger has, whether or not this run
  added any.
- 1 -- the ledger or the history could not be read or written. That never
  reads as clean: a history that silently stopped growing is the failure
  this exists to prevent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_claims import CLAIMS_PATH  # noqa: E402

HISTORY_PATH = "projects/sokrates/projects/agora/nova/resources/claims-history.json"
VAULT_TOOL = "/app/bridge/vault_tool.py"

#: Bookkeeping claims, not plans.
SKIP_PREFIXES = ("journal-seq-",)

#: The ledger fields a history row carries forward.
FIELDS = ("item", "cycle", "state", "at", "note", "outcome",
          "resumed_from", "resumed_after")


def merge(history, ledger_rows):
    """Fold ledger rows into `history` (a list, mutated). `(added, updated)`.

    Never removes a row. A row already in the history is replaced field by
    field only when the ledger's copy differs, and keeps its `first_seen_at`.
    """
    index = {(row.get("item"), row.get("cycle")): row for row in history}
    added = updated = 0
    for row in ledger_rows:
        item, cycle = row.get("item"), row.get("cycle")
        if not isinstance(item, str) or not isinstance(cycle, int):
            continue
        if item.startswith(SKIP_PREFIXES):
            continue
        fresh = {k: row[k] for k in FIELDS if k in row}
        kept = index.get((item, cycle))
        if kept is None:
            fresh["first_seen_at"] = row.get("at")
            history.append(fresh)
            index[(item, cycle)] = fresh
            added += 1
            continue
        current = {k: kept[k] for k in FIELDS if k in kept}
        if current != fresh:
            first_seen_at = kept.get("first_seen_at")
            kept.clear()
            kept.update(fresh)
            kept["first_seen_at"] = first_seen_at
            updated += 1
    history.sort(key=lambda r: (r.get("cycle", 0), r.get("first_seen_at") or ""))
    return added, updated


def vault_get(path, rev_file=None):
    """`(text, state)`: state is "ok", "absent" or "error"."""
    command = [sys.executable, VAULT_TOOL, "get", path]
    if rev_file is not None:
        command += ["--rev-file", str(rev_file)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return "", "error"
    if done.returncode != 0:
        return "", "error"
    if done.stdout.startswith("[not found"):
        return "", "absent"
    return done.stdout, "ok"


def vault_put(path, local, rev_file):
    """`None` on success, else why not. Compare-and-swap on `rev_file`."""
    try:
        done = subprocess.run(
            [sys.executable, VAULT_TOOL, "put", path, str(local),
             "--if-rev-file", str(rev_file)],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run the vault tool: {exc}"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return detail[-1] if detail else f"the vault tool exited {done.returncode}"
    return None


def report(get=vault_get, put=vault_put, workdir=None):
    """`(exit code, lines)`. Reads both documents, writes the history if it moved."""
    with tempfile.TemporaryDirectory() as scratch:
        workdir = Path(workdir or scratch)
        ledger_text, state = get(CLAIMS_PATH)
        if state != "ok":
            return 1, [f"could not read the claims ledger ({state}); the history was not touched"]
        try:
            ledger_rows = json.loads(ledger_text)["claims"]
        except (ValueError, KeyError, TypeError):
            return 1, ["the claims ledger is not the JSON shape nova_claims writes; the history was not touched"]

        rev_file = workdir / "claims-history.rev"
        history_text, state = get(HISTORY_PATH, rev_file)
        if state == "error":
            return 1, ["could not read the claims history; nothing was written"]
        history = []
        if state == "ok":
            try:
                history = json.loads(history_text)["rows"]
            except (ValueError, KeyError, TypeError):
                return 1, [f"{HISTORY_PATH} is not a {{\"rows\": [...]}} document; refusing to overwrite it"]
        before = len(history)
        added, updated = merge(history, ledger_rows)
        cycles = sorted({row["cycle"] for row in history})
        span = f"cycles {cycles[0]}..{cycles[-1]}" if cycles else "no cycles"
        summary = (f"{added} new and {updated} updated claim(s) kept; the history holds "
                   f"{len(history)} row(s) over {len(cycles)} cycle(s), {span}")
        if not added and not updated:
            return 0, [f"nothing new -- {summary}"]
        local = workdir / "claims-history.json"
        local.write_text(json.dumps({"rows": history}, indent=1, ensure_ascii=False) + "\n")
        error = put(HISTORY_PATH, local, rev_file)
        if error:
            return 1, [f"could not write the claims history ({error}); {before} row(s) stay as they were, and the ledger keeps a day for the next sweep"]
        return 0, [summary]


def main(argv=None):
    code, lines = report()
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
