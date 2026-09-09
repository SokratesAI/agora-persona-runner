"""Did a cycle's Claude Code session ever start?

`cycle_postmortem`'s `silent` bucket is a conversation with no message in
it at all. Until now the only thing that could speak to *why* was the
runner's own lifecycle ledger (`runner#939`), and that ledger cannot be
written backwards: every one of the 17 historical silent cycles reads as
"not covered", and will for as long as they stay historical.

The Claude Code CLI leaves a record that *does* reach backwards. Every
session the bridge starts writes a JSONL transcript under
``/data/claude-home/.claude/projects/<slugged cwd>/<session id>.jsonl``,
those files live on the bridge pod's own volume, and the archive here
reaches back to 2026-08-18. So for a silent cycle inside that reach there
is one question this can answer that nothing else can: **did the CLI
session start at all?**

A transcript's first line is a ``queue-operation`` carrying the prompt
that was enqueued, so a heartbeat turn is separable from the owner typing in
the chat --- and that separation is load-bearing rather than tidy. Cycle
1082's window holds a transcript whose first prompt is *"Please revert
this. I like the comment on the Journal better than this."*: a chat
session that happened to start ninety seconds after a heartbeat fired.
Counting it would have reported that cycle's CLI as having run.

**The control, measured cycle 1261 before any of this was written.**
Against every non-silent cycle Agora holds since the archive begins:
959 of 979 have a session starting in the window, 97.9%. That is what
makes an absence mean something --- a check whose negative was guaranteed
in advance measures nothing. Better still, the 20 misses are not noise:
nine of them are `cycle_postmortem`'s own `failed` rows carrying
``Connection refused`` or a read timeout against the bridge, which is
precisely a cycle whose CLI never started. The instrument and the
independent record agree on the same cycles.

And the finding it was built for: of the 16 silent cycles inside the
archive's reach, **13 started no session**. Their death is upstream of
the bridge --- the same shape as the refused-connection bucket, with
nothing recorded anywhere. Three did start one (360, 784, 1082), and
those transcripts are readable, which is the "read the transcripts before
theorising" this row has carried for days.

This changes no verdict. A conversation with no message in it is silent
whether or not a session started; this says where to look next.
"""

import datetime
import glob
import json
import os

#: Where the CLI writes its per-session transcripts on the bridge pod.
TRANSCRIPT_ROOT = "/data/claude-home/.claude/projects"

#: The prompt the runner enqueues for a heartbeat turn. A transcript whose
#: first prompt does not carry this is the owner in the chat, or a session a
#: cycle started by hand, and neither is the cycle's own turn.
HEARTBEAT_MARKER = "Automatic heartbeat trigger"

#: How far either side of a conversation's `createdAt` a session may start
#: and still be that cycle's. Agora creates the conversation first and the
#: bridge starts the session after, so the window is asymmetric on purpose;
#: the two-minute lead is for clock skew between the pods, not for a
#: session that starts before its own conversation.
BEFORE_MINUTES = 2
AFTER_MINUTES = 8


def _stamp(value):
    """One ISO instant as an aware datetime, or `None` if it will not parse."""
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None:
        return at.replace(tzinfo=datetime.timezone.utc)
    return at


def _first_row(path, opener=open):
    """The first JSON line of a transcript that carries a timestamp."""
    try:
        with opener(path, errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("timestamp"):
                    return row
    except OSError:
        return None
    return None


def index(root=TRANSCRIPT_ROOT, paths=None, read=_first_row):
    """`(sessions, reach)` --- every heartbeat session, oldest first.

    `sessions` is `[(started, path), ...]` for transcripts whose first
    enqueued prompt is a heartbeat turn. `reach` is the oldest instant in
    the archive over **all** transcripts, heartbeat or not: the archive's
    reach is a property of the files on disk, and measuring it over the
    filtered set would report a quiet stretch of chat-only sessions as
    "before the archive exists".
    """
    found = paths if paths is not None else sorted(
        glob.glob(os.path.join(root, "*", "*.jsonl")))
    sessions = []
    reach = None
    for path in found:
        row = read(path)
        if row is None:
            continue
        started = _stamp(row.get("timestamp"))
        if started is None:
            continue
        if reach is None or started < reach:
            reach = started
        content = row.get("content")
        if isinstance(content, str) and HEARTBEAT_MARKER in content:
            sessions.append((started, path))
    sessions.sort()
    return sessions, reach


def sessions_near(sessions, created,
                  before=BEFORE_MINUTES, after=AFTER_MINUTES):
    """Every heartbeat session that started in one conversation's window."""
    low = created - datetime.timedelta(minutes=before)
    high = created + datetime.timedelta(minutes=after)
    return [path for started, path in sessions if low <= started <= high]


def apply_cli_sessions(results, conversations, sessions, reach, verdict="silent"):
    """Attach a `cli_session` dict to every `silent` row, in place.

    Four answers, kept apart for the same reason the lifecycle join keeps
    its own four apart: `{"no_clock": True}` when Agora gave no
    `createdAt`, `{"uncovered": reach}` when the conversation predates the
    transcript archive, `{"paths": [...]}` when a session started, and
    `{"paths": []}` when none did. The last two are the finding; merging
    either of them into the first two would report "I could not look" as
    "nothing was there".
    """
    for row in results:
        if row.get("verdict") != verdict:
            continue
        conversation = (conversations or {}).get(row["number"]) or {}
        created = _stamp(conversation.get("createdAt"))
        if created is None:
            row["cli_session"] = {"no_clock": True}
            continue
        if reach is None or created < reach:
            row["cli_session"] = {"uncovered": reach, "created": created}
            continue
        row["cli_session"] = {"paths": sessions_near(sessions, created),
                              "created": created}
