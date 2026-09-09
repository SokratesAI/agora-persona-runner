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
archive's reach, **14 started no session**. Their death is upstream of
the bridge --- the same shape as the refused-connection bucket, with
nothing recorded anywhere. Two did start one --- 360 and 784 --- and
those transcripts are readable. (An earlier draft of this paragraph said
three and named 1082 as well; 1082 is exactly the chat session the
paragraph above says the heartbeat filter exists to exclude, so the
sentence contradicted the filter it was documenting. Corrected cycle
1262, off a live run.)

Reading those two is what `last_turn` below is for, because **"a session
DID start" turned out to cover two opposite outcomes**: cycle 360 ran to
a full reply and lost only the delivery, cycle 784 was killed 26 seconds
in having written nothing.

The `silent` verdict itself does not change --- a conversation with no
message in it is silent whatever happened upstream --- but what a cycle
should do about one now depends on which of those two it is.
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


def _blocks(row):
    """The content blocks of one transcript row, always as a list."""
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def _closing_text(row):
    """The assistant's closing prose, or `None` if the turn ends mid-flight.

    A turn whose last block is a `tool_use` is a turn still in flight: the
    model asked for something and the answer never came back. Only a turn
    that ends on non-empty `text` is a cycle that finished speaking.
    """
    blocks = _blocks(row)
    if not blocks:
        return None
    last = blocks[-1]
    if not isinstance(last, dict) or last.get("type") != "text":
        return None
    text = (last.get("text") or "").strip()
    return text or None


def _last_tool(row):
    """The name of the tool the final turn was waiting on, if any."""
    for block in reversed(_blocks(row)):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block.get("name")
    return None


def last_turn(path, opener=open):
    """How one transcript ends: `{"ran_for", "reply", "waiting_on"}`.

    `index` above answers whether a session started. That is one line
    covering two opposite outcomes, and reading the two transcripts it
    named is what separated them (cycle 1262). Cycle 360 ran for 41
    minutes, wrote its digest to the vault, composed a full reply to the
    owner --- and Agora carried none of it, so the report calls that cycle
    silent and the reply has sat unread on this disk since 2026-08-24.
    Cycle 784 was killed 26 seconds in, mid `Bash` call, having written
    nothing anywhere. Both are "a session DID start".

    So `reply` is the discriminator and it is deliberately strict: the
    **final** assistant turn must end on prose. A session that spoke and
    then went back to work and was killed there did not finish, and a
    formatter that took the last text block anywhere in the file would
    report its mid-cycle narration as a delivered reply.

    Returns `None` when the file cannot be read or holds no timestamped
    row --- an unreadable transcript is not a transcript that ends badly.
    """
    first = last = None
    final_assistant = None
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
                at = _stamp(row.get("timestamp"))
                if at is not None:
                    if first is None or at < first:
                        first = at
                    if last is None or at > last:
                        last = at
                if row.get("type") == "assistant":
                    final_assistant = row
    except OSError:
        return None
    if first is None:
        return None
    return {"ran_for": (last - first).total_seconds(),
            "reply": _closing_text(final_assistant) if final_assistant else None,
            "waiting_on": _last_tool(final_assistant) if final_assistant else None}


def apply_session_endings(results, read=last_turn, verdict="silent"):
    """Attach `endings` --- one `last_turn` per named path --- in place.

    Keyed by path rather than positional, so a transcript that could not be
    read is absent from the mapping instead of shifting the rest along.
    """
    for row in results:
        if row.get("verdict") != verdict:
            continue
        session = row.get("cli_session") or {}
        endings = {}
        for path in session.get("paths") or []:
            ending = read(path)
            if ending is not None:
                endings[path] = ending
        if endings:
            session["endings"] = endings
