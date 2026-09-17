"""How long his Stop button takes to actually stop a turn (issue #240).

`nova-kr-control-stop-seconds` is agreed as the median over five real
attempts, under ten seconds, and nothing recorded an attempt. This is the
ledger: `nova_site` appends one row every time his Stop button reaches a turn
that was running, and `tools.goal_measures` reads the median of the newest
five.

**What one row measures.** The seconds `nova_conversations.cancel` took, which
is the whole server-side path and not a fast acknowledgement: the bridge's
`/cancel` (agora-claude-bridge `bridge/cancel.py`) sends SIGTERM, polls until
the CLI process has exited, SIGKILLs it after five seconds, and only then
answers. A 200 on a turn that is still running cannot produce a row. What it
leaves out is the phone-to-server hop and his thumb finding the button.

**Only a stop that stopped something is an attempt.** "nothing was running"
means the turn ended before the tap arrived, and timing that would pull the
median towards zero with taps that stopped nothing.
"""
import json
import statistics
from datetime import datetime, timezone

STOP_TIMINGS_PATH = "projects/sokrates/projects/agora/nova/resources/stop-timings.json"

#: The agreed measure: a median over this many real attempts.
ATTEMPTS = 5


def load(text):
    """The rows in a ledger document; `[]` for an empty or new one.

    A document that is present and not the expected shape raises, because
    reading it as empty would let the next `record` overwrite it."""
    if not (text or "").strip():
        return []
    rows = json.loads(text).get("stops")
    if not isinstance(rows, list):
        raise ValueError(f"{STOP_TIMINGS_PATH} has no `stops` list")
    return rows


def dumps(rows):
    return json.dumps({"stops": rows}, indent=1) + "\n"


def row(seconds, now=None):
    now = now or datetime.now(timezone.utc)
    return {"at": now.isoformat(timespec="seconds"), "seconds": round(seconds, 2)}


def record(seconds, read=None, write=None, now=None):
    """Append one attempt. Read-modify-write on the revision it read, retried
    once: a lost race re-reads rather than overwriting the other writer."""
    if read is None or write is None:
        from agora_runner.vault import vault_read_path_rev, vault_write_path
        read = read or vault_read_path_rev
        write = write or vault_write_path
    for attempt in (1, 2):
        text, rev = read(STOP_TIMINGS_PATH)
        rows = load(text) + [row(seconds, now)]
        try:
            write(STOP_TIMINGS_PATH, dumps(rows), if_rev=rev)
            return
        except Exception:
            if attempt == 2:
                raise


def median_of_newest(rows, attempts=ATTEMPTS):
    """`(median seconds or None, detail)` over the newest `attempts` rows.

    `None` until there are that many: the agreed measure is a median over
    five, and a median over two is a different number wearing its name."""
    timed = [r for r in rows if isinstance(r.get("seconds"), (int, float))]
    timed.sort(key=lambda r: str(r.get("at", "")))
    if len(timed) < attempts:
        return None, (f"{len(timed)} of the {attempts} real stops the median "
                      f"needs are recorded -- a stop is recorded only when his "
                      f"Stop button reaches a turn that was running")
    newest = timed[-attempts:]
    value = round(statistics.median(r["seconds"] for r in newest), 2)
    return value, (f"median of the newest {attempts} stops, "
                   f"{newest[0]['at']} .. {newest[-1]['at']}: "
                   + ", ".join(f"{r['seconds']}s" for r in newest)
                   + " -- tap reaching the server to the CLI process gone; "
                     "the phone-to-server hop is not in it")
