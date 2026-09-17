"""Whether the Nova app actually loads in the browser he opens it in.

The quality-bets trial (work/product-manager/resources/quality-bets.md) left
Compatibility -- "works in your phone's browser" -- with no meter, and wrote
it down as a manual check at each review because no cycle can open his phone.
The owner, 0256140f, 2026-09-17 16:00: *"implement analytics that run in the
background when i open the app ... so i do not have to do any manual tasks. I
hate manual tasks."*

So the page reports on itself. A small inline script in `index.html`, written
in the oldest JavaScript any browser still runs and placed before `app.js`,
catches every uncaught error from the moment the page starts and, a few
seconds after load, posts whether `app.js` got as far as marking itself
booted. It has to sit outside `app.js`: a browser that cannot parse `app.js`
is exactly the failure being measured, and that browser would never run a
reporter living inside it.

**One row per day per browser, not per open.** The document is rewritten on
every open, so a row per open would make every open cost more than the last.
A day's opens from one user agent fold into one row with counts, which keeps
the document proportional to the number of distinct browsers he uses.
"""
import json
from datetime import datetime, timezone

APP_OPENS_PATH = "projects/sokrates/projects/agora/nova/resources/app-opens.json"

#: The longest user agent or error message kept. Both come from a request
#: body, so an unbounded one would be written into the vault verbatim.
MAX_TEXT = 300


def load(text):
    """The rows in a ledger document; `[]` for an empty or new one.

    A document that is present and not the expected shape raises, because
    reading it as empty would let the next `record` overwrite it."""
    if not (text or "").strip():
        return []
    rows = json.loads(text).get("opens")
    if not isinstance(rows, list):
        raise ValueError(f"{APP_OPENS_PATH} has no `opens` list")
    return rows


def dumps(rows):
    return json.dumps({"opens": rows}, indent=1) + "\n"


def report(payload):
    """A request body -> `(booted, errors)`, or None when it is not a report.

    `booted` must be a real boolean: a missing one is not "it failed", and
    counting it as a failure would score his phone broken on a malformed
    request."""
    if not isinstance(payload, dict) or not isinstance(payload.get("booted"), bool):
        return None
    errors = payload.get("errors")
    errors = [str(e)[:MAX_TEXT] for e in errors if str(e).strip()] if isinstance(errors, list) else []
    return payload["booted"], errors


def fold(rows, user_agent, booted, errors, now=None):
    """`rows` with one open added to today's row for this user agent."""
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    ua = (user_agent or "(no user agent)")[:MAX_TEXT]
    at = now.isoformat(timespec="seconds")
    rows = list(rows)
    for r in rows:
        if r.get("day") == day and r.get("ua") == ua:
            break
    else:
        r = {"day": day, "ua": ua, "opens": 0, "booted": 0, "with_errors": 0}
        rows.append(r)
    r["opens"] = r.get("opens", 0) + 1
    r["booted"] = r.get("booted", 0) + (1 if booted else 0)
    r["last_at"] = at
    if errors:
        r["with_errors"] = r.get("with_errors", 0) + 1
        r["last_errors"] = errors[:3]
    return rows


def record(user_agent, booted, errors, read=None, write=None, now=None):
    """Add one open. Read-modify-write on the revision it read, retried once:
    a lost race re-reads rather than overwriting the other writer."""
    if read is None or write is None:
        from agora_runner.vault import vault_read_path_rev, vault_write_path
        read = read or vault_read_path_rev
        write = write or vault_write_path
    for attempt in (1, 2):
        text, rev = read(APP_OPENS_PATH)
        rows = fold(load(text), user_agent, booted, errors, now)
        try:
            write(APP_OPENS_PATH, dumps(rows), if_rev=rev)
            return
        except Exception:
            if attempt == 2:
                raise
