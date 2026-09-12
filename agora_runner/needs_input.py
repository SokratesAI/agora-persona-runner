"""Asking Edvard something, in a conversation of its own, instead of the digest.

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

The SENDER is `Nova`, never `Edvard`. `decide_turn` makes the curator speak
whenever the last visible message came from him, so posting the question under
his name would have the persona answer my own question to itself -- and it
would put words in his mouth in his own transcript. `nova_ask` posts as
`Edvard` for exactly the opposite reason: there, he really did type it.

This module lives in `agora_runner/` rather than `tools/` because it needs
`AGORA_TOKEN`, which the bridge pod (the `Bash` shell) does not hold and the
runner pod does -- and the runner image ships `agora_runner/` only. Run it
from `terminal_exec`:

    python -m agora_runner.needs_input --cycle 1443 \
        --question 'Yes or no, should X?' --context 'why I am asking'
"""

import argparse
import sys

from agora_runner.http_util import agora_internal
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


def ask(question, context, cycle=None):
    """Open (or re-use) the conversation for this question and post into it.

    Returns `(ok, info)`. `info` is a dict on success carrying `conversationId`,
    `name` and `repeat` (True when the thread already existed), and an
    explanatory string on failure -- the caller is a cycle, and a cycle that
    cannot ask needs to say so in its journal rather than silently not ask.
    """
    problem = validate(question, context)
    if problem:
        return False, problem

    name = conversation_name(question)
    status, body = agora_internal("POST", "/conversations", {
        "name": name,
        "personaId": ANSWER_PERSONA_ID,
    })
    if status not in (200, 201):
        log(f"needs_input: create conversation failed HTTP {status}")
        return False, f"could not open the conversation (HTTP {status})"
    conversation = body.get("conversation") or {}
    cid = conversation.get("id")
    if not cid:
        return False, "Agora answered with no conversation id"
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

    text = opening_message(question, context, cycle=cycle, repeat=repeat)
    # `sender` is what keeps the curator quiet -- see the module docstring.
    # `push` is left at its default: the buzz on his phone is the entire
    # reason this exists rather than a line in the digest.
    status, posted = agora_internal("POST", f"/conversations/{cid}/notify", {
        "text": text, "sender": "Nova", "system": False})
    message_id = (posted.get("message") or {}).get("id")
    if status not in (200, 201) or not message_id:
        log(f"needs_input: notify failed HTTP {status}")
        return False, f"opened {name} but could not post the question (HTTP {status})"
    return True, {"conversationId": cid, "name": name, "repeat": repeat,
                  "messageId": message_id}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ask Edvard something in a conversation of its own (issues.md #209).")
    parser.add_argument("--question", required=True,
                        help="the ask itself, ending in a question mark")
    parser.add_argument("--context", required=True,
                        help="why you are asking -- enough to stand alone")
    parser.add_argument("--cycle", help="your cycle number, for the signature")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the conversation name and message, post nothing")
    args = parser.parse_args(argv)

    problem = validate(args.question, args.context)
    if problem:
        print(problem)
        return 2
    if args.dry_run:
        print(conversation_name(args.question))
        print()
        print(opening_message(args.question, args.context, cycle=args.cycle))
        return 0
    ok, info = ask(args.question, args.context, cycle=args.cycle)
    if not ok:
        print(info)
        return 1
    what = "posted into the existing thread" if info["repeat"] else "opened"
    print(f"{what}: {info['name']}  ({info['conversationId']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
