"""A written record of whether each ask actually buzzed the owner's phone.

`nova-kpi-push-delivered` -- "your phone buzzes when Nova asks", floor 100% --
had no instrument because the one place that fact exists is Agora's answer to
a post: `status: "sent"`, or a `quietHours` / `muted` / `watching` flag saying
why it held the push back. `needs_input.ask` and `nudge_ask.nudge` read that
answer once through `push_held` and then dropped it, and the stored message
carries no push field (checked on thread `0256140f`, Cycle 1689). So this
writes one line per post, into a vault document, at the moment the answer is
in hand.

A nudge is recorded as well as the ask it re-announces, because a nudge is
what finally reaches him when the ask itself was posted inside quiet hours.
`delivery_share` counts an ask as reaching him when the ask or any later post
into the same thread went out.

A failed write never fails the ask: the question is already in the thread and
the buzz already happened or did not. It is returned so the caller can say so.
"""

import json
from datetime import datetime, timedelta, timezone

PATH = "projects/sokrates/projects/agora/nova/resources/ask-push-log.md"
MARKER = "## Records"
WINDOW = timedelta(days=7)

#: Held reasons that still mean he saw it. Agora withholds the push when the
#: thread is already open on his screen, so a buzz would have told him nothing.
SEEN_WITHOUT_A_PUSH = ("he had the thread on screen in another app",)


def _append(path, content, after_marker):
    # Imported here so importing this module never pulls in CouchDB config.
    from agora_runner.vault import vault_append_path
    return vault_append_path(path, content, after_marker=after_marker)


def record_line(kind, conversation_id, message_id, held, cycle=None, now=None):
    now = now or datetime.now(timezone.utc)
    return json.dumps({
        "ts": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "conversationId": conversation_id,
        "messageId": message_id,
        "pushed": held is None,
        "held": held,
        "cycle": cycle,
    }, sort_keys=True)


def record(kind, conversation_id, message_id, held, cycle=None, now=None):
    """Write one outcome. Returns None when written, else why it was not."""
    line = record_line(kind, conversation_id, message_id, held, cycle, now)
    try:
        result = _append(PATH, line, MARKER)
    except Exception as exc:  # the ask has already happened; never undo it
        return f"could not record the push outcome: {exc}"
    if str(result).startswith("FAILED"):
        return f"could not record the push outcome: {result}"
    return None


def parse(text):
    """Every record in the document. A line that is not a record is skipped."""
    records = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
            row["when"] = datetime.fromisoformat(row["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        records.append(row)
    return records


def _reached(row):
    return row.get("pushed") is True or row.get("held") in SEEN_WITHOUT_A_PUSH


def delivery_share(records, until=None):
    """Share of asks in the last seven days that reached him. `(value, detail)`.

    `None` when no ask falls in the window: a share over nothing is not 100%,
    and on a floor of 100 it is not 0% either.
    """
    until = until or datetime.now(timezone.utc)
    since = until - WINDOW
    asks = [r for r in records
            if r.get("kind") == "ask" and since <= r["when"] <= until]
    if not asks:
        first = min((r["when"] for r in records), default=None)
        begins = (f"records begin {first.isoformat(timespec='minutes')}"
                  if first else "the log holds no record yet")
        return None, f"no ask was opened in the last 7 days ({begins})"
    missed = []
    for ask in asks:
        later = [r for r in records
                 if r.get("conversationId") == ask.get("conversationId")
                 and ask["when"] <= r["when"] <= until]
        if not any(_reached(r) for r in later):
            missed.append(ask)
    reached = len(asks) - len(missed)
    detail = f"{reached} of {len(asks)} ask(s) in 7 days reached his phone"
    if missed:
        detail += "; never reached: " + ", ".join(
            f"{r.get('conversationId')} ({r.get('held')})" for r in missed)
    return round(100.0 * reached / len(asks), 1), detail
