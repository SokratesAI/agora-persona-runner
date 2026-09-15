"""Has the owner answered one of my open questions, and is anyone still waiting?

    python3 -m tools.ask_watch

`agora_runner.needs_input` is how a cycle asks him something: it opens an
Agora conversation named after the question, tags it `nova:needs-input`, and
buzzes his phone. That is half a mailbox. **Nothing reads the answer.**

The only thing carrying an open question from one cycle to the next is a
sentence in `journal-digest.md` with a thread id hand-copied into it, and it
narrows: the seven newest handoff lines name
`0256140f-1b68-437b-b1c7-6a4267c43e05` and no other ask, while
`18bdb05e-2ad0-479d-9a7d-d9b8bab3fd5e` and `3f42afbc-668a-45c8-8ed3-60313edd37e6`
have carried only my own messages since 2026-09-13 and dropped out of the
handoff after 09-14. Measured against the live store 2026-09-15 07:02 Oslo:
five threads, three of them still waiting. If he had replied in either of the
two the digest stopped naming, the reply would have reached nobody — and a
question I asked and then could not hear the answer to is worse than one I
never asked, because I told him it would be picked up.

**The predicate is "the newest message is not mine", not "there is more than
one message".** A thread he answered and a cycle then acted on is finished, and
the cycle's own reply is what says so: `ee039370` and `05250907` both carry his
answer *and* my response, and both read green here. That makes this check
self-clearing rather than red forever — the `--repair`-style trap where a
condition, once true, can never go back. Archiving the thread clears it too,
since an archived conversation is dropped by `?active=true`.

**A waiting thread does not raise.** Opening a question and waiting on him is
the mechanism working, not a defect — the same call `project_goals_check` makes
on a goal that is still being discussed. It is printed with its age because a
question that has been open thirty-six hours is worth seeing, and it is
counted on the summary line, which is all `preflight` shows on a clean check.

**"He never answered" and "he answered and a cycle replied" are separated, and
the separation costs the whole message list.** Both end on a message of mine,
so the newest message alone cannot tell them apart — the first version of this
read one message per thread and reported all five as waiting on him, two of
them at 58 and 71 hours, when he had in fact answered both and a cycle had
closed them out. A count of five things he is ignoring, three of which he is
not, is a worse instrument than no count. So the window is the last 200
messages: a thread longer than that is read from its tail, which can only ever
show a settled thread as waiting (the newest message is always in the tail),
never the reverse.

Two threads it deliberately cannot see. One he archived: that is his "I am done
with this", and second-guessing it would make the check argue with him. And one
whose answer he typed somewhere else entirely — a board comment, a note — which
is the same known hole `top_board_rows` has, not something to fix by loosening
the match.

Exit codes: 0 nothing of mine is unanswered, 1 a thread could not be read (no
instrument is not no answer), 2 he has answered and no cycle has picked it up.
"""

import argparse
import sys
from datetime import datetime, timezone

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.http_util import agora_get
from agora_runner.needs_input import NAME_PREFIX, NEEDS_INPUT_TAG, SENDER


def _is_ask(row):
    """Tag or name, because the two fail in different directions.

    `needs_input` tags a thread only when it creates it, and logs rather than
    raises when the PATCH fails — so the tag can be missing from a real ask.
    The name is the de-duplication key and is therefore always right, but he
    can rename a thread from the drawer. Either one is enough.
    """
    if NEEDS_INPUT_TAG in (row.get("tags") or []):
        return True
    return str(row.get("name") or "").startswith(NAME_PREFIX)


def _age_hours(ts, now):
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).total_seconds() / 3600.0


# The tail this reads. See the docstring: long enough that no ask this loop has
# ever opened comes close, and a thread past it degrades in the safe direction.
WINDOW = 200


def messages(conversation_id):
    """The thread's last `WINDOW` messages, oldest first, or None with a reason.

    Agora publishes no `GET /conversations/:id`, so the message listing is how
    a thread's state is read — same route `nova_conversations` uses.
    """
    status, body = agora_get(
        f"/conversations/{conversation_id}/messages?limit={WINDOW}")
    if status != 200:
        return None, f"messages returned HTTP {status}"
    rows = (body or {}).get("messages")
    if not isinstance(rows, list):
        return None, "the listing carried no messages array"
    if not rows:
        return None, "the thread is empty"
    return rows, None


def check(now=None):
    """Returns (answered, waiting, settled, unreadable)."""
    now = now or datetime.now(timezone.utc)
    status, body = agora_get("/conversations?active=true")
    if status != 200:
        return None, None, None, [
            ("(the listing)", "", f"conversation listing returned HTTP {status}")]

    answered, waiting, settled, unreadable = [], [], [], []
    for row in (body or {}).get("conversations") or []:
        cid = row.get("id")
        if not cid or row.get("archived") or not _is_ask(row):
            continue
        name = row.get("name") or "(unnamed)"
        rows, problem = messages(cid)
        if problem:
            unreadable.append((name, cid, problem))
            continue
        newest = rows[-1]
        sender = str(newest.get("sender") or "").strip()
        age = _age_hours(newest.get("ts"), now)
        if not sender:
            # Not "he has not answered": a message with no sender is one this
            # check cannot attribute, and calling that mine would silently
            # swallow his reply.
            unreadable.append((name, cid, "the newest message names no sender"))
        elif sender != SENDER:
            answered.append((name, cid, sender, age, str(newest.get("text") or "")))
        elif any(str(m.get("sender") or "").strip() not in ("", SENDER) for m in rows):
            settled.append((name, cid, age))
        else:
            waiting.append((name, cid, age))
    return answered, waiting, settled, unreadable


def _age(hours):
    if hours is None:
        return "age unknown"
    if hours < 1:
        return f"{hours * 60:.0f} min ago"
    return f"{hours:.1f}h ago"


def report(answered, waiting, settled, unreadable, out=sys.stdout):
    if answered is None:
        for name, _cid, problem in unreadable:
            print(f"COULD NOT READ {name}: {problem}", file=out)
        print("No reading — that is no instrument, not no answer.", file=out)
        return 1

    for name, cid, sender, age, text in answered:
        print(f"ANSWERED — {name}", file=out)
        print(f"  {cid}  last word from {sender}, {_age(age)}", file=out)
        print(f"  {' '.join(text.split())}", file=out)
        print("  Read the thread and reply in it; your reply is what clears this.", file=out)
    for name, cid, problem in unreadable:
        print(f"COULD NOT READ — {name}", file=out)
        print(f"  {cid}  {problem}", file=out)
    for name, cid, age in waiting:
        print(f"waiting on him — {name}", file=out)
        print(f"  {cid}  asked {_age(age)}, he has not written in it", file=out)

    if unreadable:
        print(f"Could not read {len(unreadable)} open ask(s) — that is no "
              "instrument, not no answer.", file=out)
        return 1
    if answered:
        print(f"{len(answered)} of my open ask(s) have an answer nobody has "
              f"picked up; {len(waiting)} still waiting on him and "
              f"{len(settled)} answered and closed out.", file=out)
        return 2
    print(f"Nothing he answered is sitting unread; {len(waiting)} open ask(s) "
          f"still waiting on him and {len(settled)} answered and closed out.",
          file=out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.parse_args(argv)
    return report(*check())


if __name__ == "__main__":
    sys.exit(main())
