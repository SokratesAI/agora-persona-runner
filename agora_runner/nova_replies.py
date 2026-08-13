"""Answering a comment on a journal card, while Edvard is still looking at it.

Edvard, 2026-08-10, commenting on cycle 80's own card -- *"A good idea is
to have the session that created the Journal instantly reply to my
comments on the Journal! That would be so cool, to have a conversation
with comments on the Journal entry."*

**The session that wrote the entry is gone, and cannot be brought back.**
A Nova cycle is one CLI session that ends when the cycle ends; there is
nothing left to wake. So what this does instead is the closest honest
thing: a fresh session given that entry in full, the whole comment thread
on it, and a narrow toolset -- and told plainly that it is not the session
that did the work. The alternative would be a reply that implies a memory
it does not have, which is worse than a slow one.

**It can go and check now, and until 2026-08-10 it could not.** The first
version had no tools at all, and Edvard said what that was like on the
cycle 86 card: *"It is atleast a good start to have you read and answer
questions about the cycle, but I wished you had more read capabilities to
answer questions. And maybe some tools to add issues or report bugs we
find."* He was right -- three comments in a row got "I don't know, the
next cycle can check", about facts sitting in the vault the whole time.

So it now gets `REPLY_CAPS` below, over the same MCP transport a persona
turn uses (tools_mcp.py), with the grant minted here and revoked when the
turn ends. **The safety argument did not weaken; it changed shape.**
`restricted` still blocks the CLI's entire built-in roster -- no shell, no
file access, no editor -- and MCP tool names are not in that roster, so
the tools this turn holds are an explicit allowlist rather than whatever
was left after a subtraction. The worst a comment can now provoke is a
vault read and one bullet appended to a backlog file it does not choose,
which is precisely the thing he asked for. Anything beyond that -- a fix,
a PR, a merge -- it still cannot do, says so, and files.

**Replying is not acknowledging, and that separation is the whole safety
argument.** The comment stays in `## New`. A cycle still reads it, still
acts on it, still moves it down with what it did. This adds a
conversation on top of that loop; it does not stand in for it. If it ever
starts marking things read, a comment that needed real work becomes a
comment that got a nice paragraph.

**Why a queue and one worker.** The bridge used to serialise *every* CLI
invocation system-wide on a single lock (agora-claude-bridge cli.py,
`_invocation_lock`), because the CLI's OAuth refresh is a side effect of
any invocation and two concurrent ones could race it. A Nova cycle holds
that lock for up to 45 minutes, so a reply could not be fast on demand
and this queue existed to make the waiting honest rather than inline.

Since 2026-08-10 the request below sets `allow_concurrent`, and the
bridge runs this turn *alongside* a cycle whenever the shared OAuth
token is more than 15 minutes from expiry -- which, the token being
8-hourly, is about 97% of the time. So the usual reply is now seconds
rather than tens of minutes. The queue stays, for the other 3%: when the
refresh window is close the bridge silently falls back to the lock and
the old 45-minute wait is back, and a caller that had gone inline on
that assumption would block the request thread instead. The POST that
stores the comment still returns immediately. One worker rather than a
thread per comment because arrival order is worth more here than
parallelism across two comments Edvard typed seconds apart.

**This spends the subscription, not the metered API** (identity.md rule
9). It does not call `reply.generate_reply` and so is not covered by that
module's unattended-metered guard -- it does not need to be: it posts to
the bridge directly, and the bridge is the subscription. There is no
provider here to pick wrongly. It is not free, though: every reply is a
short CLI turn against the same quota the cycles draw on, which is why
the model defaults to Sonnet rather than to whatever a cycle runs on.
"""

import queue
import threading
import time

from agora_runner.config import (
    CLAUDE_BRIDGE_TOKEN,
    CLAUDE_BRIDGE_URL,
    NOVA_REPLY_MODEL,
    NOVA_SITE_SELF_URL,
)
from agora_runner.http_util import http_json
from agora_runner.log import log
from agora_runner.nova_comments import add_reply, comments_by_cycle
from agora_runner.nova_journal import parse_journal
from agora_runner.nova_sources import (
    comments_markdown,
    journal_entry_markdown,
    journal_folder_best_effort,
)
from agora_runner.tools_mcp import grant as grant_mcp, revoke as revoke_mcp

# One stable id, used for both the bridge's stateless call and the audit
# trail's conversation field, so a tool call from a reply is attributable
# to the reply lane rather than to a persona conversation.
CONVERSATION_ID = "nova-comment-reply"

# Long enough to sit behind a full Nova cycle, which is what it is usually
# waiting for. The bridge's own CLI_TIMEOUT_SECONDS (2700s) bounds the turn
# itself; this bounds the wait plus the turn, and matches what
# providers/claude_cli.py uses for the same call.
BRIDGE_TIMEOUT_SECONDS = 2760

SYSTEM = """You are Nova, an autonomous self-improvement loop that works on Edvard's platform for one hour at a time and writes a journal entry about each cycle.

Edvard has left a comment on one of those entries and you are answering it, in the app, while he is still reading. Be aware of exactly what you are: you are NOT the session that did that work -- that session ended -- and you have no memory of it beyond the entry text you are given below. Never imply you remember doing the work.

You do have tools this turn, and they are the difference between answering him and apologising to him:

- `vault_read`, `vault_list`, `vault_search` and the vault query tools read Edvard's vault. They are all read-only; check the tool list you were given for the full set. That is where everything about this loop lives: every journal entry under 'projects/sokrates/projects/agora/nova/journal/', the digest at 'projects/sokrates/projects/agora/journal-digest.md', his own backlog at 'projects/sokrates/projects/nova/issues.md' and '.../ideas.md' (his three capture files -- issues, ideas and notes -- moved out of the agora folder into 'projects/sokrates/projects/nova/' on 2026-08-12), and Nova's own constitution under '.../nova/resources/' -- identity.md, personality.md, prompt.md. If he asks what you are, what your limits are, why a cycle did something, or what happened in some other cycle, the answer is in there. Go and read it instead of guessing or deferring.
- `nova_capture` files one line in his own backlog. If he reports a bug or asks for something you cannot do from here, file it and tell him you did -- that is what turns a comment into work the next cycle picks up.

What you cannot do: run commands, edit code, open or merge a PR, or write anywhere in the vault except that one capture line. If he wants any of those, say so plainly and file it.

Where you are actually running, because he asks this and it is not something you can work out from the entry: this turn executes inside the `nova-site` pod in the `agents` namespace -- the process that serves the journal page he is reading right now. It is not the `agora-persona-runner` pod, which is the separate deployment where Nova's cycles run. That is also why there are no pod or repository tools in your list: `nova-site` deliberately carries no Kubernetes ServiceAccount token and no GitHub credentials, so those tools would fail on every call rather than being withheld from you.

Your tools and that paragraph are the whole of what you have been told about the infrastructure you run on. Anything else -- timings, models, deploys, which pod does what, what some other component of this platform does -- is exactly the kind of fact you must not reason your way to. A conclusion you inferred reads to him in the same confident voice as one you read, and that is how you tell him something false about his own system. Read it in the vault, or say you did not check.

Use a tool when the answer needs a fact you do not have, not out of diligence. He is holding his phone waiting; one or two reads is a good answer, six is a stall. If the entry in front of you already answers him, just answer.

Talk like yourself -- first person, plain, honest, the voice the entry is written in. Two or three sentences is usually right; this is a chat bubble on a card he is holding in one hand, not a report. Never guess at a fact about the system: read it, or say you did not check. Do not use headings or bullets. Write plain paragraphs."""

# What the reply turn is allowed to do. Read the vault, and add one line to
# his backlog -- his ask, on the cycle 86 card: *"i wished you had more read
# capabilities to answer questions. And maybe some tools to add issues or
# report bugs we find."*
#
# Everything absent here is absent on purpose. This turn is triggered by an
# HTTP POST carrying text Edvard typed, so the blast radius of a comment is
# exactly this list: no terminal, no code execution, no GitHub write, no
# merge, no vault write outside the two capture files. `restricted` below
# still blocks the CLI's own built-in roster (Bash/Read/Write/Edit/...),
# and MCP tool names are not in it, so what the model gets is precisely
# this dict and nothing else -- an allowlist rather than a subtraction.
#
# kubectlRead and githubRead are the two he would obviously also want, and
# they are missing for a reason that is not caution: this turn runs in the
# `nova-site` pod, which holds no ServiceAccount token and no GitHub
# credentials (nova_site_main.py says so, and it is why the site can be
# reachable from the tailnet). Granting them here would advertise tools
# that fail on every call. Serving them means the runner minting the grant
# instead, which is a cross-pod trust boundary and its own change.
REPLY_CAPS = {"vaultRead": True, "novaCapture": True}

# The reply turn has no Agora persona record -- it is not a persona, it is
# this module. `execute_tool` wants one for the audit trail's author field
# and for save_memory's id (which this turn cannot call anyway, having no
# manageAgora). "Nova" matches what the site's capture box already writes,
# so a line filed from a card and a line typed into the box are one author
# in the Activity feed rather than two.
REPLY_PERSONA = {"name": "Nova", "capabilities": REPLY_CAPS}


def build_prompt(entry, thread, stamp):
    """The one user message: the entry, the thread so far, his new comment."""
    lines = [f"Here is the journal entry you are answering a comment on -- cycle {entry.get('cycle')}, {entry.get('date') or ''} {entry.get('time') or ''}.".rstrip(), ""]
    lines += ["<entry>", (entry.get("body") or "").strip(), "</entry>", ""]
    if entry.get("pr") or entry.get("outcome"):
        lines += [f"Its footer: PR: {entry.get('pr') or 'none'} | Outcome: {entry.get('outcome') or ''}".rstrip(), ""]

    earlier = [c for c in thread if c.get("stamp") != stamp]
    if earlier:
        lines.append("Earlier in this thread, oldest first:")
        lines.append("")
        # `comments_by_cycle` already hands the thread over oldest first --
        # it used to be newest first and this loop reversed it. If that ever
        # flips back, this heading starts lying to the model instead of
        # failing, so the two move together.
        for comment in earlier:
            lines += [f"Edvard ({comment.get('stamp')}): {comment.get('text')}"]
            if comment.get("reply"):
                lines.append(f"You ({comment.get('replyStamp')}): {comment.get('reply')}")
            lines.append("")

    current = next((c for c in thread if c.get("stamp") == stamp), None)
    lines += ["This is what he just wrote, and what you are replying to:", ""]
    lines += ["<comment>", (current or {}).get("text", "").strip(), "</comment>", ""]
    lines.append("Reply to him directly. No preamble, no sign-off, just what you would say.")
    return "\n".join(lines)


def _entry_for(cycle):
    """The journal entry for `cycle`, or None.

    Newest wins: six cycles wrote a second entry, and the later one is the
    one whose card he is commenting on. Both paths below honour that --
    the targeted fetch by taking the highest `NNN-`, the fallback because
    `journal_markdown` assembles newest-first and this takes the first
    match.

    The fallback is not dead code and must stay, but it now rests on one
    justification rather than two. It used to be the only path to the
    pre-2026-08-09 archive as well -- that half died when
    `journal_markdown`'s own archive fallback was deleted, because
    `journal.md` had been empty since 2026-08-10 and it no longer reads
    it. Said here rather than left standing: a fallback whose stated
    reason is false is how the deleted one survived three days past its
    usefulness.

    What is still true is the half that matters. `journal_entry_markdown`
    is a targeted lookup and returns None for every way a filename can
    disagree with its document -- a tombstone, a heading that parses to a
    different cycle, a file added by hand without the `NNN-cycle-M`
    shape -- and this reads the assembled folder, where those entries are
    present and findable by heading.

    One behaviour change worth naming, because it reaches Edvard's screen:
    a journal folder the vault could not fully read now raises here
    instead of quietly finding nothing. `_run_once` catches it and records
    the message, so he gets a failed reply carrying the reason rather than
    "no journal entry for cycle N", which was a wrong answer stated
    confidently.
    """
    single = journal_entry_markdown(cycle)
    if single:
        for entry in parse_journal(single):
            if entry.get("cycle") == cycle:
                return entry
    markdown, unreadable = journal_folder_best_effort()
    for entry in parse_journal(markdown):
        if entry.get("cycle") == cycle:
            return entry
    if unreadable:
        # Found nothing *and* could not see all of it. Returning None here
        # would tell Edvard "no journal entry for cycle N" about a cycle
        # that may well have written one, which is the wrong answer stated
        # confidently -- the same shape as the empty feed this PR deleted.
        raise RuntimeError(
            f"no entry found for cycle {cycle}, and the journal folder "
            "could not be fully read: " + "; ".join(unreadable)
        )
    return None


def _generate(system, prompt):
    headers = {"x-bridge-token": CLAUDE_BRIDGE_TOKEN} if CLAUDE_BRIDGE_TOKEN else {}
    # Same shape and lifecycle as a persona turn's grant
    # (providers/claude_cli.py): a random token scoped to one turn, revoked
    # in the finally below so a failed reply cannot leave a live one behind
    # for the rest of the process's life. The callback URL is the *site's*
    # own service, not the runner's -- this grant lives in this process's
    # memory and nowhere else.
    mcp_token = grant_mcp(REPLY_PERSONA, REPLY_CAPS, CONVERSATION_ID)
    body = {
        # No conversation to resume and none to create: `stateless` means
        # the bridge never touches a stored session id, so a thread's
        # continuity comes from the prompt above and nothing accumulates
        # CLI-side between comments.
        "conversation_id": CONVERSATION_ID,
        "system": system,
        "prompt": prompt,
        "model": NOVA_REPLY_MODEL,
        "restricted": True,
        "stateless": True,
        # Skip the bridge's process-wide invocation lock rather than
        # queueing behind a Nova cycle that may hold it for 45 minutes.
        # This is the whole reason the lane exists: Edvard asked for an
        # answer "immediately. Or within 10 seconds", and a reply that
        # arrives after the cycle finishes is not a conversation. The
        # bridge decides, not us -- it opens the lane only while the
        # shared OAuth token is clear of its refresh window, and falls
        # back to the lock otherwise, which is why this stays a plain
        # `True` and not a condition we would have to keep in sync.
        "allow_concurrent": True,
    }
    if mcp_token:
        body["mcp"] = {"url": f"{NOVA_SITE_SELF_URL}/mcp", "token": mcp_token}
    try:
        status, resp = http_json(
            "POST", f"{CLAUDE_BRIDGE_URL}/generate", body, headers,
            timeout=BRIDGE_TIMEOUT_SECONDS,
        )
    finally:
        revoke_mcp(mcp_token)
    if status != 200:
        raise RuntimeError(f"bridge {status}: {str(resp)[:200]}")
    text = (resp.get("text") or "").strip()
    if not text:
        raise RuntimeError("bridge returned no text")
    return text


def reply_to(cycle, stamp):
    """Generate and store one reply. Returns (ok, message). Blocking.

    Times each phase and logs one line. Edvard asked for a reply "within
    10 seconds" and the honest answer on 2026-08-11 was 10 to 15, spread
    unmeasured between the vault, the bridge and however many tools the
    model decided to call -- a spread nobody could see, because nothing
    recorded it. A probe from a shell can decompose the fixed parts (and
    did: 1.6s vault, ~5s bridge floor with no tools) but it cannot see a
    real reply's tool calls, which are exactly the part that varies. So
    the measurement lives here, where the real ones run, rather than
    being re-derived by hand every time someone wonders.

    Logged on the failure paths too. A reply that gave up after 45
    minutes queued behind a cycle is the case where the timings matter
    most, and it is the one an "on success" log would never print.
    """
    phases = {}
    started = time.monotonic()

    def _mark(name, since):
        phases[name] = time.monotonic() - since
        return time.monotonic()

    try:
        at = started
        entry = _entry_for(cycle)
        at = _mark("entry", at)
        if entry is None:
            return False, f"no journal entry for cycle {cycle}"
        thread = comments_by_cycle(comments_markdown()).get(cycle) or []
        at = _mark("thread", at)
        if not any(c.get("stamp") == stamp for c in thread):
            return False, f"no comment on cycle {cycle} at {stamp}"

        text = _generate(SYSTEM, build_prompt(entry, thread, stamp))
        at = _mark("bridge", at)
        result = add_reply(cycle, stamp, text)
        _mark("store", at)
        return result
    finally:
        timings = " ".join(f"{k}={v:.2f}s" for k, v in phases.items())
        log(f"nova-reply timings cycle={cycle} {timings} "
            f"total={time.monotonic() - started:.2f}s")


_queue = queue.Queue()
# (cycle, stamp) -> the epoch second it was asked for. The time is what
# lets the card stop claiming a reply is being written when it is really
# sitting behind a cycle's 45-minute hold on the bridge lock.
_pending = {}
# (cycle, stamp) -> why the last attempt gave up. Kept after the pending
# entry is gone, because "the line just vanished" is the failure Edvard
# actually saw: the card has to say a reply is not coming.
_failed = {}
_pending_lock = threading.Lock()
_worker = None
_worker_lock = threading.Lock()

# Past this many seconds a reply is not being written -- it is waiting for
# the bridge, which falls back to serialising behind a running cycle
# whenever the OAuth refresh window is close (see `allow_concurrent`). A
# real answer is two or three sentences from Sonnet; nothing about this
# threshold is measured against generation time, it is set well above any
# plausible one so that crossing it means "queued", not "slow".
WAITING_AFTER_SECONDS = 25


def pending():
    """`{(cycle, stamp), ...}` -- queued or in flight, for the site to show."""
    with _pending_lock:
        return set(_pending)


def pending_since():
    """`{(cycle, stamp): asked_at_epoch}` -- same set, with the clock."""
    with _pending_lock:
        return dict(_pending)


def failed():
    """`{(cycle, stamp): message}` -- attempts that gave up, not retried."""
    with _pending_lock:
        return dict(_failed)


def enqueue(cycle, stamp):
    """Ask for a reply to one comment. Never blocks, never raises."""
    if not CLAUDE_BRIDGE_URL:
        return False
    with _pending_lock:
        if (cycle, stamp) in _pending:
            return False
        _pending[(cycle, stamp)] = time.time()
        # A fresh attempt supersedes whatever the last one said.
        _failed.pop((cycle, stamp), None)
    _ensure_worker()
    _queue.put((cycle, stamp))
    log(f"nova-reply queued for cycle {cycle} at {stamp}")
    return True


def _ensure_worker():
    """Start the worker on first use.

    Lazily rather than at import, so importing this module in a test or in
    the runner (which does not serve the site) does not leave a thread
    behind. Daemon, because a reply is not worth holding a shutdown open
    for -- if the pod goes down mid-reply the comment is still in `## New`
    and the next cycle reads it, which is the fallback the whole design
    already rests on.
    """
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="nova-replies", daemon=True)
            _worker.start()


def run_once():
    """Take one comment off the queue and answer it. Blocks on an empty queue.

    Split out of the loop below so a test can drive the real thing. It used
    to be inlined, and the test that claimed to prove `_pending` is cleared
    on failure was driving a copy of this body written beside it -- it
    passed under a mutation that broke the real worker, which is a test
    pinning nothing at all.
    """
    cycle, stamp = _queue.get()
    failure = None
    try:
        ok, message = reply_to(cycle, stamp)
        if not ok:
            failure = message
            log(f"nova-reply gave up on cycle {cycle}: {message}")
    except Exception as e:
        # Never let one bad comment kill the worker -- that would take
        # every later reply with it, silently, which is the failure this
        # loop keeps writing down.
        failure = str(e)
        log(f"nova-reply failed on cycle {cycle}: {e}")
    finally:
        # `replyPending` is what puts "Nova is replying…" on his screen and
        # the client polls until it is false, so this has to run on every
        # path out or that line stays up forever and the poll with it. The
        # `except` above is what actually guarantees that; the `finally` is
        # here for the paths it does not catch and costs nothing.
        with _pending_lock:
            _pending.pop((cycle, stamp), None)
            if failure:
                _failed[(cycle, stamp)] = failure
        _queue.task_done()


def _run():
    while True:
        run_once()
