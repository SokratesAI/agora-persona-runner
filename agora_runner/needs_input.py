"""Asking the owner something, in a conversation of its own, instead of the digest.

His standing instruction, `issues.md` #209, 2026-09-10: retire **Needs input**
in `journal-digest.md` as the mechanism for surfacing a question to him.
A cycle that needs an answer should open a NEW Agora conversation that prompts
him directly, and he answers there. Two reasons he gave, both structural:

- A cycle dies after its ~45 minute window, so it cannot wait for a reply the
  way a live conversation can. A line in the digest waits for him to go and
  read the digest; a conversation buzzes his phone and is still there when he
  gets to it.
- He wants the answer to land somewhere easy to reply to, not routed back
  into the heartbeat's own transient conversation.

His one requirement: **the new conversation must not be empty.** It needs
"the correct context from the cycle session, like a fork of that session", so
he is not asked to re-derive what is being asked. `POST /conversations/:id/fork`
exists on Agora's public app, but it forks an existing thread's messages --
a heartbeat cycle's own conversation is a transcript of tool calls, not an
explanation, so forking it would hand him the wrong context at great length.
What this does instead is the approximation cycle 1354 recorded as buildable:
create, then immediately post an opening message carrying the question and
enough of the cycle's own reasoning to stand alone.

**Two things here are deliberately not tags.**

The conversation NAME is derived from the question, and `POST /conversations`
answers `200 {"status": "exists"}` for a name it already has. That is the
whole of the de-duplication: a second cycle that hits the same wall asks the
same question, computes the same name, and its message lands in the thread he
is already looking at rather than opening a second one. It is idea #229's
"cycles that hit the same problem comment on the existing card instead of
creating a new one", and it costs no new state anywhere.

The SENDER is `Nova`, never his own name. `decide_turn` makes the curator speak
whenever the last visible message came from him, so posting the question under
his name would have the persona answer my own question to itself -- and it
would put words in his mouth in his own transcript. `nova_ask` posts under
his name for exactly the opposite reason: there, he really did type it.

This module lives in `agora_runner/` rather than `tools/` because it needs
`AGORA_TOKEN`, which the bridge pod (the `Bash` shell) does not hold and the
runner pod does -- and the runner image ships `agora_runner/` only. Run it
from `terminal_exec`:

    python -m agora_runner.needs_input --cycle 1443 \
        --question 'Yes or no, should X?' --context 'why I am asking'

Exit 4 means a live thread is already on the topic and it is named: post into
it with `--into <id>`, or pass `--new-thread` and say why it is separate.
"""

import argparse
import re
import sys

from agora_runner import ask_push_log
from agora_runner.http_util import agora_get, agora_internal, unauthorized_hint
from agora_runner.log import log
from agora_runner.nova_conversations import ANSWER_PERSONA_ID


# The thread is found again by its exact name, so this prefix is part of the
# de-duplication key and changing it orphans every open question.
NAME_PREFIX = "Nova needs you — "

# Long enough for a real question, short enough to read as a title on a phone.
# A question longer than this keeps its full text in the opening message; only
# the name is cut.
MAX_NAME_QUESTION_CHARS = 90

# So idea #229's needs-input page has something to select on later. The name
# is what de-duplicates; this is only a label.
NEEDS_INPUT_TAG = "nova:needs-input"

# Who the opening message is from. Named rather than inlined because
# `tools.ask_watch` reads it back: "the newest message is not from me" is how
# it tells an answered thread from one still waiting, and a second copy of
# this string would make every thread read as answered the day one of them
# changed.
SENDER = "Nova"


def _one_line(text):
    return " ".join(text.split())


def validate(question, context):
    """The reason this refuses rather than warns is in `personality.md`:
    an ask opens with the question, and it says what kind of answer it wants.
    Six of the eight asks ever written opened with a statement instead, so the
    mechanical half of that rule is enforced here the same way `lint_entry`
    enforces it on a journal entry."""
    if not isinstance(question, str) or not question.strip():
        return "a question is required"
    if not _one_line(question).endswith("?"):
        return "the question has to end in a question mark -- open with the ask, not with the situation"
    if not isinstance(context, str) or not context.strip():
        return "context is required -- he must not have to re-derive what is being asked"
    return None


def conversation_name(question):
    """Deterministic in the question, because it is the de-duplication key."""
    short = _one_line(question)
    if len(short) > MAX_NAME_QUESTION_CHARS:
        short = short[:MAX_NAME_QUESTION_CHARS - 1].rstrip() + "…"
    return NAME_PREFIX + short


def opening_message(question, context, cycle=None, repeat=False):
    """The question first, on its own line, then the reason.

    `repeat` is what a second cycle asking the same thing posts. It says so
    out loud: without it, two identical-looking messages in one thread read as
    a bug rather than as a second cycle hitting the same wall.
    """
    lines = []
    if repeat:
        who = f"Cycle {cycle}" if cycle else "Another cycle"
        lines.append(f"**{who} needs this too, and is still waiting.**")
        lines.append("")
    lines.append(f"**{_one_line(question)}**")
    lines.append("")
    lines.append(context.strip())
    lines.append("")
    lines.append(_footer(cycle))
    return "\n".join(lines)


def _footer(cycle):
    who = f"Nova, cycle {cycle}" if cycle else "Nova"
    return f"— {who}. Reply here and the next cycle picks it up."


# -- is this already being discussed somewhere? (issues.md #234) ------------
#
# The exact-name match above only catches the same question asked twice in the
# same words. His #234 is the other case: he had started on a topic in "Manual
# feedback & improvements" and I opened a second thread on it, forking the
# discussion. So before a new thread is opened, the live threads where he has
# actually written, and my earlier asks, are read and matched against the
# question. A match refuses the new thread and names the old one; the cycle
# then posts into it with `--into`, or says why it is separate with
# `--new-thread`.

# Newest rows read per thread. Most rows in a live thread are `activity`
# (streamed tool steps, dropped below): the #780 ask thread's newest 30
# rows held no message at all, so 30 found nothing. The listing carries no
# text, so each thread costs one GET: ~22 on 2026-09-19, about 15 seconds.
RELATED_MESSAGES = 300

# Words too common in my own asks to say anything about the topic.
_STOPWORDS = frozenset(
    "about after again because before being could every first would should "
    "their there these those which while where whether other still since "
    "until today think thing things there's doesn't cannot really right "
    "shall want yours never always".split())

_REF = re.compile(r"(?:[\w.-]+)?#\d+")


def topic_terms(text):
    """(words, refs): lowercase words of 5+ letters that are not stopwords,
    and issue/PR references like `#234` or `platform-config#780`."""
    text = text or ""
    refs = {r.lower() for r in _REF.findall(text)}
    words = {w for w in re.findall(r"[a-zA-Z][a-zA-Z'-]{4,}", text.lower())
             if w not in _STOPWORDS}
    return words, refs


def related_score(question, thread_text):
    """How strongly a thread's text is about the question, or 0.

    A shared reference (`#780`, `platform-config#780`) is decisive on its own:
    two threads naming the same PR are about the same thing. Otherwise it
    takes at least three shared topic words and at least half of the
    question's, so a long thread does not match everything by sheer size.
    """
    q_words, q_refs = topic_terms(question)
    t_words, t_refs = topic_terms(thread_text)
    bare = lambda refs: {r[r.index("#"):] for r in refs}
    shared_refs = q_refs & t_refs or bare(q_refs) & bare(t_refs)
    shared_words = q_words & t_words
    if shared_refs:
        return 100 + len(shared_words)
    if len(shared_words) >= max(3, -(-len(q_words) // 2)):
        return len(shared_words)
    return 0


def _is_candidate(conversation, own_name):
    tags = conversation.get("tags") or []
    if conversation.get("archived") or conversation.get("name") == own_name:
        return False
    # A cycle's own transcript is tool calls, not a discussion.
    return not any(str(t).startswith("evolve-cycle:") for t in tags)


def related_threads(question):
    """Live threads already on this question's topic, best match first.

    Only what someone other than me wrote counts, plus the thread's name,
    except in my earlier asks, where my own message is the topic: a
    heartbeat's transcript is me talking to myself and would match every
    topic I work on. Returns (threads, problem); a listing that
    cannot be read is a problem, never an empty answer, because "I could not
    look" must not read as "nothing related".
    """
    own_name = conversation_name(question)
    status, body = agora_get("/conversations?active=true")
    if status != 200 or not isinstance(body, dict):
        return [], f"could not list conversations (HTTP {status})"
    found = []
    for c in body.get("conversations") or []:
        if not c.get("id") or not _is_candidate(c, own_name):
            continue
        is_ask = NEEDS_INPUT_TAG in (c.get("tags") or [])
        mstatus, mbody = agora_get(
            f"/conversations/{c['id']}/messages?limit={RELATED_MESSAGES}")
        if mstatus != 200 or not isinstance(mbody, dict):
            continue
        messages = [m for m in mbody.get("messages") or []
                    if isinstance(m, dict) and not m.get("activity")]
        # What he wrote, plus the name. My own replies in a chat thread run to
        # tens of KB and match any topic by size: measured on "Manual feedback
        # & improvements", a question about Marcus's calendar colours shared
        # three of its four words with my replies there. In an ask thread my
        # messages ARE the topic, so they count.
        said = [m for m in messages if is_ask or m.get("sender") != SENDER]
        text = " ".join([c.get("name") or ""] + [str(m.get("text") or "") for m in said])
        score = related_score(question, text)
        if score:
            found.append({"conversationId": c["id"], "name": c.get("name") or "",
                          "score": score, "lastMessageAt": c.get("lastMessageAt") or ""})
    found.sort(key=lambda t: (t["score"], t["lastMessageAt"]), reverse=True)
    return found, None


def ask(question, context, cycle=None, into=None, new_thread=False):
    """Open (or re-use) the conversation for this question and post into it.

    Returns `(ok, info)`. `info` is a dict on success carrying `conversationId`,
    `name` and `repeat` (True when the thread already existed), and an
    explanatory string on failure -- the caller is a cycle, and a cycle that
    cannot ask needs to say so in its journal rather than silently not ask.

    `into` posts the question into an existing thread instead of opening one.
    Without it, a thread already on the topic refuses the ask with
    `info = {"related": [...]}` unless `new_thread` is set.
    """
    problem = validate(question, context)
    if problem:
        return False, problem

    if into:
        cid, name, repeat = into, None, False
    else:
        if not new_thread:
            related, unreadable = related_threads(question)
            if unreadable:
                return False, (f"{unreadable} -- cannot check whether this is "
                               "already being discussed; pass --new-thread to ask anyway")
            if related:
                return False, {"related": related}
        cid, name, repeat, problem = _open(question)
        if problem:
            return False, problem
    return _post(question, context, cycle, cid, name, repeat)


def _open(question):
    name = conversation_name(question)
    status, body = agora_internal("POST", "/conversations", {
        "name": name,
        "personaId": ANSWER_PERSONA_ID,
    })
    if status not in (200, 201):
        log(f"needs_input: create conversation failed HTTP {status}")
        return None, name, False, f"could not open the conversation (HTTP {status}{unauthorized_hint(status)})"
    conversation = body.get("conversation") or {}
    cid = conversation.get("id")
    if not cid:
        return None, name, False, "Agora answered with no conversation id"
    # 200 is `findByName` handing back the thread that was already there.
    repeat = status == 200

    if not repeat:
        # Only on the thread's first message: re-tagging an existing thread
        # would overwrite tags he has set from the Settings drawer, and
        # `nova:mute` is one of them.
        tag_status, _ = agora_internal("PATCH", f"/conversations/{cid}", {
            "tags": [NEEDS_INPUT_TAG]})
        if tag_status not in (200, 201):
            log(f"needs_input: could not tag conversation {cid} (HTTP {tag_status})")
    return cid, name, repeat, None


def _post(question, context, cycle, cid, name, repeat):
    text = opening_message(question, context, cycle=cycle, repeat=repeat)
    # `sender` is what keeps the curator quiet -- see the module docstring.
    # `push` is left at its default: the buzz on his phone is the entire
    # reason this exists rather than a line in the digest.
    status, posted = agora_internal("POST", f"/conversations/{cid}/notify", {
        "text": text, "sender": SENDER, "system": False})
    message_id = (posted.get("message") or {}).get("id")
    if status not in (200, 201) or not message_id:
        log(f"needs_input: notify failed HTTP {status}")
        return False, (f"opened {name or cid} but could not post the question "
                       f"(HTTP {status}{unauthorized_hint(status)})")
    held = push_held(posted)
    if held:
        log(f"needs_input: {cid} posted but the push was withheld ({held})")
    # Written now because Agora's answer is the only place this fact exists
    # (nova-kpi-push-delivered). A failed write is reported, never fatal.
    unrecorded = ask_push_log.record("ask", cid, message_id, held, cycle=cycle)
    if unrecorded:
        log(f"needs_input: {cid} {unrecorded}")
    return True, {"conversationId": cid, "name": name, "repeat": repeat,
                  "messageId": message_id, "pushed": held is None,
                  "pushHeld": held, "unrecorded": unrecorded}


# What Agora answers when it appended the message and deliberately did not
# buzz his phone. Keyed on the flag rather than on `status`, because
# `status: "recorded"` is the same word for all three (agora src/server.ts).
PUSH_HELD_REASONS = {
    "quietHours": "quiet hours -- 22:00 to 07:00 Europe/Oslo",
    "muted": "the thread is tagged nova:mute",
    "watching": "he had the thread on screen in another app",
}


def push_held(response):
    """Why his phone did not buzz for a message Agora accepted, or None.

    An ask exists to reach him, so "appended, not announced" is not the same
    outcome as "sent" and must not read like it. Nothing here read this body
    at all, which is how thread `0256140f` -- the twelve objectives, opened
    02:22 on 2026-09-15, inside quiet hours -- was reported as asked by seven
    consecutive cycles while his phone never made a sound. His capture of
    2026-09-15 is the only reason I know: *"Never got a notification for the
    ask thread from cycle 1617 ... my silence is a symptom of them not
    reaching me, not me ignoring them."*
    """
    if not isinstance(response, dict):
        # Not evidence either way, and the safe reading of "I cannot tell" on
        # a measure whose whole point is reaching him is "he was not reached".
        return "Agora answered with no body to read"
    for flag, reason in PUSH_HELD_REASONS.items():
        if response.get(flag) is True:
            return reason
    if str(response.get("status") or "") == "sent":
        return None
    if response.get("error"):
        return str(response["error"])
    # An unknown shape is not evidence that the push went out. Say so rather
    # than reporting a buzz nobody can show happened.
    return "Agora did not say the push was sent"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ask the owner something in a conversation of its own (issues.md #209).")
    parser.add_argument("--question", required=True,
                        help="the ask itself, ending in a question mark")
    parser.add_argument("--context", required=True,
                        help="why you are asking -- enough to stand alone")
    parser.add_argument("--cycle", help="your cycle number, for the signature")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the conversation name and message, post nothing")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--into", metavar="CONVERSATION_ID",
                       help="post into this existing thread instead of opening one")
    where.add_argument("--new-thread", action="store_true",
                       help="open a new thread even though a related one is live")
    args = parser.parse_args(argv)

    problem = validate(args.question, args.context)
    if problem:
        print(problem)
        return 2
    if args.dry_run:
        print(conversation_name(args.question))
        print()
        print(opening_message(args.question, args.context, cycle=args.cycle))
        if not args.into and not args.new_thread:
            related, unreadable = related_threads(args.question)
            print()
            print(unreadable or f"related live threads: {len(related)}")
            for t in related:
                print(f"  {t['conversationId']}  {t['name']}  (score {t['score']})")
        return 0
    ok, info = ask(args.question, args.context, cycle=args.cycle,
                   into=args.into, new_thread=args.new_thread)
    if not ok and isinstance(info, dict) and info.get("related"):
        print("NOT ASKED -- this looks like it is already being discussed "
              "(issues.md #234):")
        for t in info["related"]:
            print(f"  {t['conversationId']}  {t['name']}  (score {t['score']}, "
                  f"last message {t['lastMessageAt'] or 'never'})")
        print("Post into one of them with --into <id>, or pass --new-thread "
              "and say in the context why this is a separate question.")
        return 4
    if not ok:
        print(info)
        return 1
    if args.into:
        what = "posted into"
    else:
        what = "posted into the existing thread" if info["repeat"] else "opened"
    print(f"{what}: {info['name'] or 'thread'}  ({info['conversationId']})")
    if info.get("unrecorded"):
        print(f"WARNING: {info['unrecorded']} -- nova-kpi-push-delivered will not count this ask")
    if info.get("pushHeld"):
        print(f"HIS PHONE DID NOT BUZZ — {info['pushHeld']}. The question is in "
              "the thread and he has no idea it is there. `python3 -m "
              "tools.ask_watch --nudge` re-announces it once it is audible again.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
