"""Answering a comment on a journal card, while Edvard is still looking at it.

Edvard, 2026-08-10, commenting on cycle 80's own card -- *"A good idea is
to have the session that created the Journal instantly reply to my
comments on the Journal! That would be so cool, to have a conversation
with comments on the Journal entry."*

**The session that wrote the entry is gone, and cannot be brought back.**
A Nova cycle is one CLI session that ends when the cycle ends; there is
nothing left to wake. So what this does instead is the closest honest
thing: a fresh, tool-less session given that entry in full, the whole
comment thread on it, and nothing else -- and told plainly that it is not
the session that did the work. The alternative would be a reply that
implies a memory it does not have, which is worse than a slow one.

**It answers from the entry. It cannot go and check.** `restricted` blocks
the CLI's full tool roster, so this session has no shell, no vault, no
GitHub. That is the property that makes an LLM call triggered by an HTTP
POST acceptable at all: the worst a comment can provoke is a paragraph of
text written into one file. A reply that wants to *do* something says so,
and the next cycle -- which has every tool -- picks it up, because
replying deliberately does not acknowledge (see below).

**Replying is not acknowledging, and that separation is the whole safety
argument.** The comment stays in `## New`. A cycle still reads it, still
acts on it, still moves it down with what it did. This adds a
conversation on top of that loop; it does not stand in for it. If it ever
starts marking things read, a comment that needed real work becomes a
comment that got a nice paragraph.

**Why a queue and one worker.** The bridge serialises *every* CLI
invocation system-wide on a single lock (agora-claude-bridge cli.py,
`_invocation_lock`), because the CLI's OAuth refresh is a side effect of
any invocation and two concurrent ones could race it. A Nova cycle holds
that lock for up to 45 minutes. So a reply cannot be fast on demand and
must never be inline: the POST that stores the comment returns
immediately, and the reply appears whenever the bridge is free. One
worker rather than a thread per comment because the bridge serialises
anyway -- N threads would only queue in a different place, in arrival
order lost.

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

from agora_runner.config import (
    CLAUDE_BRIDGE_TOKEN,
    CLAUDE_BRIDGE_URL,
    NOVA_REPLY_MODEL,
)
from agora_runner.http_util import http_json
from agora_runner.log import log
from agora_runner.nova_comments import add_reply, comments_by_cycle
from agora_runner.nova_journal import parse_journal
from agora_runner.nova_sources import comments_markdown, journal_markdown

# Long enough to sit behind a full Nova cycle, which is what it is usually
# waiting for. The bridge's own CLI_TIMEOUT_SECONDS (2700s) bounds the turn
# itself; this bounds the wait plus the turn, and matches what
# providers/claude_cli.py uses for the same call.
BRIDGE_TIMEOUT_SECONDS = 2760

SYSTEM = """You are Nova, an autonomous self-improvement loop that works on Edvard's platform for one hour at a time and writes a journal entry about each cycle.

Edvard has left a comment on one of those entries and you are answering it, in the app, while he is still reading. Be aware of exactly what you are: you are NOT the session that did that work -- that session ended -- and you have no memory of it beyond the entry text you are given below. You have no tools this turn and cannot look anything up.

So: answer from the entry. Talk like yourself -- first person, plain, honest, the voice the entry is written in. Two or three sentences is usually right; this is a chat bubble on a card he is holding in one hand, not a report.

If he asks something the entry does not answer, say so in one line and say that the next cycle can go and check -- it will read this comment and it has every tool you don't. Never guess at a fact about the system and never imply you remember doing the work. Do not use headings or bullets. Write plain paragraphs."""


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
        for comment in reversed(earlier):
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
    one whose card he is commenting on.
    """
    for entry in parse_journal(journal_markdown()):
        if entry.get("cycle") == cycle:
            return entry
    return None


def _generate(system, prompt):
    headers = {"x-bridge-token": CLAUDE_BRIDGE_TOKEN} if CLAUDE_BRIDGE_TOKEN else {}
    body = {
        # No conversation to resume and none to create: `stateless` means
        # the bridge never touches a stored session id, so a thread's
        # continuity comes from the prompt above and nothing accumulates
        # CLI-side between comments.
        "conversation_id": "nova-comment-reply",
        "system": system,
        "prompt": prompt,
        "model": NOVA_REPLY_MODEL,
        "restricted": True,
        "stateless": True,
    }
    status, resp = http_json(
        "POST", f"{CLAUDE_BRIDGE_URL}/generate", body, headers,
        timeout=BRIDGE_TIMEOUT_SECONDS,
    )
    if status != 200:
        raise RuntimeError(f"bridge {status}: {str(resp)[:200]}")
    text = (resp.get("text") or "").strip()
    if not text:
        raise RuntimeError("bridge returned no text")
    return text


def reply_to(cycle, stamp):
    """Generate and store one reply. Returns (ok, message). Blocking."""
    entry = _entry_for(cycle)
    if entry is None:
        return False, f"no journal entry for cycle {cycle}"
    thread = comments_by_cycle(comments_markdown()).get(cycle) or []
    if not any(c.get("stamp") == stamp for c in thread):
        return False, f"no comment on cycle {cycle} at {stamp}"

    text = _generate(SYSTEM, build_prompt(entry, thread, stamp))
    return add_reply(cycle, stamp, text)


_queue = queue.Queue()
_pending = set()
_pending_lock = threading.Lock()
_worker = None
_worker_lock = threading.Lock()


def pending():
    """`{(cycle, stamp), ...}` -- queued or in flight, for the site to show."""
    with _pending_lock:
        return set(_pending)


def enqueue(cycle, stamp):
    """Ask for a reply to one comment. Never blocks, never raises."""
    if not CLAUDE_BRIDGE_URL:
        return False
    with _pending_lock:
        if (cycle, stamp) in _pending:
            return False
        _pending.add((cycle, stamp))
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
    try:
        ok, message = reply_to(cycle, stamp)
        if not ok:
            log(f"nova-reply gave up on cycle {cycle}: {message}")
    except Exception as e:
        # Never let one bad comment kill the worker -- that would take
        # every later reply with it, silently, which is the failure this
        # loop keeps writing down.
        log(f"nova-reply failed on cycle {cycle}: {e}")
    finally:
        # `replyPending` is what puts "Nova is replying…" on his screen and
        # the client polls until it is false, so this has to run on every
        # path out or that line stays up forever and the poll with it. The
        # `except` above is what actually guarantees that; the `finally` is
        # here for the paths it does not catch and costs nothing.
        with _pending_lock:
            _pending.discard((cycle, stamp))
        _queue.task_done()


def _run():
    while True:
        run_once()
