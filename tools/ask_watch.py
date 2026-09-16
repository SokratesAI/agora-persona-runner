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

One thread it deliberately cannot see: the one he archived. That is his "I am
done with this", and second-guessing it would make the check argue with him.

**An answer that landed in a different conversation is no longer invisible.**
That paragraph used to call it the same known hole `top_board_rows` has, and it
was not the same hole — a board comment is in another store, another Agora
thread is three messages away in this one. On 2026-09-15 he answered two open
asks in `Manual feedback & improvements` rather than in their own threads and
told me why: *"it feels like you are still split into multiple personas where
your cycles are one and your chats are another."* This check printed `he has
not written in it` for both. So when an ask is waiting, every active thread
that is not an ask and has moved since is read, and anything HE wrote there is
printed as `HE HAS BEEN TALKING ELSEWHERE` and raises. It costs nothing at all
when nothing is waiting, and 5.9s over 39 threads when something is (measured
against the live store, same morning). A board comment and a note are still
holes and are still not this.

**And a waiting thread is not always a thread he has seen.** Agora appends
the message and withholds the phone buzz during quiet hours -- 22:00 to 07:00
Europe/Oslo, the default in `agora`'s `src/config.ts`, and nothing in the
`agents` namespace overrides it. Nothing retries it afterwards, so an ask
opened at 02:22 reaches him only if he goes looking. That is thread
`0256140f`, the twelve objectives: seven consecutive cycles wrote "still
unanswered" into the handoff about a question his phone never mentioned. His
capture, 2026-09-15: *"Never got a notification for the ask thread from cycle
1617 ... my silence is a symptom of them not reaching me, not me ignoring
them."* So a waiting thread whose newest message landed inside quiet hours is
reported separately, as `NEVER REACHED HIS PHONE`, and `--nudge` re-announces
it.

**The predicate is the state, which is why this needs no ledger.** A nudge is
posted only while it is audible, so the thread's newest message is then
outside quiet hours and the predicate is false for good. It fires once per
silenced ask and cannot loop.

**A goal discussion is a question waiting on him, and this could not see
one.** On 2026-09-16 this printed `0 open ask(s) still waiting on him` while
`project_goals_check` printed eleven of eleven projects still discussing
their objective and twenty-five key results unanswered. Neither tool was
lying: `needs_input` tags a thread `nova:needs-input`, a goal conversation is
opened by `project_goal_thread` and is not tagged, so `_is_ask` was false for
every one of them. A dead instrument and an honest all-clear print the same
0, and this loop read that 0 for days.

So the threads named by `project-goals.md` are swept too, and **the two
counts have to reconcile**: every project still `discussing` is either a
thread judged here, a project arguing in no thread at all, or a pointer this
cannot resolve -- and the summary line says which, so the number of open
questions can never be smaller than the number of undecided projects without
saying why.

**A goal thread is never `settled`, and that is the difference from an ask.**
An ask is finished when he answers and a cycle replies; a goal is finished
when the document says `agreed` or `struck`. A thread where he wrote and I
wrote back still has an undecided objective hanging off it, so it counts as
waiting on him until the document moves. Reading it as settled is exactly the
mistake that produced the 0.

Exit codes: 0 nothing of mine is unanswered, 1 a thread could not be read (no
instrument is not no answer), 2 something needs a cycle to act -- he has
answered and nobody picked it up, an ask is waiting that never reached his
phone, or he has been writing in another thread while an ask of mine waits.
"""

import argparse
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.http_util import agora_get, agora_internal, unauthorized_hint
from agora_runner.needs_input import (
    NAME_PREFIX, NEEDS_INPUT_TAG, SENDER, push_held)
from agora_runner.project_goals import (
    discussion_threads, match_thread_id, parse_project_goals)

# Where the goals this loop is still arguing with him are written down.
GOALS_PATH = "projects/sokrates/projects/nova/project-goals.md"
VAULT_CLIENT = "/app/bridge/vault_tool.py"


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

# Agora's own defaults, from `agora`'s `src/config.ts`:
# `QUIET_HOURS_START ?? "22:00"`, `QUIET_HOURS_END ?? "07:00"`,
# `QUIET_HOURS_TZ ?? "Europe/Oslo"`. Copied rather than read because Agora
# publishes no route that answers them; verified against the live cluster on
# 2026-09-15 -- no container in `agents` sets any of the three, so the
# defaults are what is running. If that ever changes there, change it here.
QUIET_START_MINUTE = 22 * 60
QUIET_END_MINUTE = 7 * 60
QUIET_TZ = "Europe/Oslo"


def _oslo_minutes(when):
    """Minutes since local midnight in Oslo, DST included."""
    return (when.astimezone(ZoneInfo(QUIET_TZ)).hour * 60
            + when.astimezone(ZoneInfo(QUIET_TZ)).minute)


def in_quiet_hours(when):
    """Was `when` inside the window where Agora withholds the phone buzz?

    Half-open on both ends, the same as Agora's `isQuiet`, so a message at
    exactly 07:00 is audible and one at exactly 22:00 is not. The window wraps
    midnight, which is why this is not a single comparison.
    """
    if when is None:
        return False
    minutes = _oslo_minutes(when)
    return minutes >= QUIET_START_MINUTE or minutes < QUIET_END_MINUTE


def _parse_ts(ts):
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


NUDGE_TEXT = (
    "**This question is still open and your phone never mentioned it** — I "
    "posted it during quiet hours, so Agora filed the message and withheld the "
    "buzz, and nothing retried it. Nothing above has changed; scroll up for the "
    "ask itself. — Nova, re-announcing once."
)


def resolve(conversation_id, because, rows=None):
    """Close an ask whose answer arrived somewhere other than its own thread.

    This is the half `spoken_elsewhere` left unfinished. That function can say
    "he has been talking elsewhere"; nothing could say "and the answer is in
    there, and I acted on it". So an ask he settled in another conversation
    stayed in `waiting` for good -- thread 18bdb05e asked whether five projects
    should stop being projects, he answered it in **Manual feedback &
    improvements** at 06:57 on 2026-09-15, three cycles executed the answer in
    full, and 37 hours later this tool still told me he owed me a word. Every
    cycle read that and none of them could clear it.

    Deciding that an answer arrived elsewhere is judgement and stays mine.
    Recording it is not, which is why it is here: the closing message goes into
    the ask thread so the question on his phone stops reading as unanswered,
    and the thread is archived so `check` drops it.

    **Post first, archive second, and never the other way round.** If the
    archive fails the closing message is still in the thread and the ask still
    reads open -- visible and recoverable. If the archive landed and the post
    failed, the ask would be gone from every list with nothing saying why.

    Returns (ok, detail). Archiving is reversible: PATCH `archived: false`.
    """
    because = (because or "").strip()
    if not because:
        # A closed ask with no reason is exactly the thing this replaces.
        return False, "refused: --because is empty, and a closed ask with no reason is worse than an open one"
    if rows is not None and not any(
            str(m.get("sender") or "").strip() == SENDER for m in rows):
        return False, "refused: no message of mine in this thread, so it is not an ask of mine to close"

    text = (f"**Closed — you answered this, just not in here.** {because}\n\n"
            "Nothing above has changed and nothing is being asked of you. "
            "— Nova, closing the thread out.")
    # No `push_held` check, unlike `nudge`: a withheld buzz on a close is not
    # a failure. Nothing is being asked of him, so the message only has to be
    # in the thread when he next opens it.
    status, _ = agora_internal(
        "POST", f"/conversations/{conversation_id}/notify",
        {"text": text, "sender": SENDER, "system": False})
    if status not in (200, 201):
        return False, (f"nothing posted and nothing archived: notify returned "
                       f"HTTP {status}{unauthorized_hint(status)}")

    status, _ = agora_internal(
        "PATCH", f"/conversations/{conversation_id}", {"archived": True})
    if status not in (200, 201, 204):
        return False, (f"the closing message is posted but the archive returned "
                       f"HTTP {status}{unauthorized_hint(status)} — the ask still "
                       f"reads as waiting")
    return True, "closed out and archived"


def nudge(conversation_id, text=NUDGE_TEXT):
    """Post the re-announcement, so the ask gets the one push it never got.

    Returns (ok, detail). Deliberately a normal message rather than a
    `system: true` one: a system notice is machinery talking and Nova's thread
    filters those out of what it renders, which is the opposite of what an ask
    he has not seen needs.
    """
    status, body = agora_internal(
        "POST", f"/conversations/{conversation_id}/notify",
        {"text": text, "sender": SENDER, "system": False})
    if status not in (200, 201):
        return False, f"notify returned HTTP {status}{unauthorized_hint(status)}"
    held = push_held(body)
    if held:
        return False, f"posted but the push was withheld again ({held})"
    return True, "his phone buzzed"


# His own name as Agora records it, the same literal `agora_runner/nova_ask.py`
# and `agora_runner/conversations.py` already match on. Deliberately not
# "anybody who is not Nova": `K3s Sentinel` writes in its own thread every
# morning and it is not him.
OWNER = "Edvard"

# The tail read from a conversation that is not an ask, when looking for him.
# Shorter than WINDOW on purpose: this only ever asks "did he write here since
# a moment I already know", and the answer lives at the end of the thread.
ELSEWHERE_WINDOW = 30


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


def spoken_elsewhere(others, since, now=None):
    """Threads that are not asks where HE has written since `since`.

    An ask of mine is answered when he writes in its own thread, and that is
    the only place this check used to look. On 2026-09-15 he answered two of
    them somewhere else -- he had the same discussion running in `Manual
    feedback & improvements` and said so in the ask itself: *"I am actually
    already discussing this with you in the conversation with title ... it
    feels like you are still split into multiple personas where your cycles
    are one and your chats are another."* At 09:10 this check printed `he has
    not written in it` for both, which is true and reads as silence. It was
    not silence; the answer was three messages away in the same store.

    `others` is the listing rows for every active conversation that is not an
    ask, so nothing here costs a call until an ask is actually waiting -- and
    only threads whose `lastMessageAt` is past `since` are opened at all.
    Measured against the live store 2026-09-15: 39 threads had moved, and
    reading all of them took 5.9s.

    Returns (hits, unreadable), newest first. A hit is
    (name, cid, ts, text).
    """
    hits, unreadable = [], []
    for row in others:
        cid = row.get("id")
        if not cid:
            continue
        moved = _parse_ts(row.get("lastMessageAt"))
        if moved is None or since is None or moved <= since:
            continue
        status, body = agora_get(
            f"/conversations/{cid}/messages?limit={ELSEWHERE_WINDOW}")
        name = row.get("name") or "(unnamed)"
        if status != 200:
            unreadable.append((name, cid, f"messages returned HTTP {status}"))
            continue
        for m in (body or {}).get("messages") or []:
            if str(m.get("sender") or "").strip() != OWNER:
                continue
            when = _parse_ts(m.get("ts"))
            if when is None or when <= since:
                continue
            hits.append((name, cid, when, str(m.get("text") or "")))
    hits.sort(key=lambda h: h[2], reverse=True)
    return hits, unreadable


def read_goals(path=None):
    """The `project-goals.md` markdown, or (None, reason, from_this_pod).

    `from_this_pod` is False when the vault client is simply not on this
    filesystem -- `ask_watch` runs inside `preflight` on the bridge pod,
    where it is, and a cycle running this by hand from the runner pod has no
    vault at all. That is `nas_health`'s `CANNOT SEE FROM THIS POD` and it
    deliberately does not raise: no pull request fixes running on the wrong
    pod. A client that *is* there and failed is a real unreadable.
    """
    if path:
        try:
            return pathlib.Path(path).read_text(), None, True
        except OSError as exc:
            return None, f"{path}: {exc}", True
    if not pathlib.Path(VAULT_CLIENT).exists():
        return None, "no vault client on this pod", False
    try:
        done = subprocess.run(
            [sys.executable, VAULT_CLIENT, "get", GOALS_PATH],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"vault_tool.py get failed: {exc}", True
    if done.returncode != 0:
        return None, f"vault_tool.py exited {done.returncode}", True
    text = done.stdout
    # The vault client answers a missing document with a marker and exit 0.
    if not text.strip() or "[not found]" in text[:200]:
        return None, f"{GOALS_PATH} came back empty or missing", True
    return text, None, True


def goal_discussions(markdown):
    """`(project, cid_as_written, pending_key_results)` per undecided project."""
    return discussion_threads(parse_project_goals(markdown))


def check(now=None, goals_markdown=None, goals_problem=None,
          goals_unreadable=None):
    """Returns (answered, waiting, silenced, settled, unreadable, elsewhere).

    `elsewhere` is where HE has been talking while an ask of mine waits -- see
    `spoken_elsewhere`. It is empty, and costs no call at all, unless something
    is actually waiting.

    `silenced` is the subset of waiting threads whose newest message -- mine,
    by definition of waiting -- landed inside quiet hours, so his phone was
    never told about it. They are not in `waiting` as well: a thread is in
    exactly one list, because the two ask for different things from a cycle.
    """
    now = now or datetime.now(timezone.utc)
    status, body = agora_get("/conversations?active=true")
    if status != 200:
        return None, None, None, None, [
            ("(the listing)", "", f"conversation listing returned HTTP {status}")], [], None

    answered, waiting, silenced, settled, unreadable = [], [], [], [], []
    others = []
    rows_by_id = {}
    for row in (body or {}).get("conversations") or []:
        cid = row.get("id")
        if cid and not row.get("archived"):
            rows_by_id[str(cid)] = row

    # Resolve each undecided project onto a live thread first, so the sweep
    # below knows which conversations carry a goal argument rather than
    # discovering it per row.
    goal_rows = goal_discussions(goals_markdown) if goals_markdown else []
    goals = None
    if goals_markdown or goals_problem or goals_unreadable:
        goals = {"problem": goals_problem, "unreadable": goals_unreadable,
                 "rows": goal_rows, "unargued": [], "unresolved": [],
                 "by_thread": {}}
    for project, written, pending in goal_rows if goals else ():
        if not written:
            goals["unargued"].append((project, pending))
            continue
        resolved = match_thread_id(written, rows_by_id)
        if resolved is None:
            goals["unresolved"].append((project, written, pending))
            continue
        goals["by_thread"].setdefault(resolved, []).append((project, pending))

    by_thread = goals["by_thread"] if goals else {}
    for cid, row in rows_by_id.items():
        if cid in by_thread:
            continue  # judged below, on the stricter goal predicate
        if not _is_ask(row):
            others.append(row)
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
        elif in_quiet_hours(_parse_ts(newest.get("ts"))):
            silenced.append((name, cid, age))
        else:
            waiting.append((name, cid, age))

    # A goal thread is judged on the document's status, not on who spoke
    # last: `agreed`/`struck` is the only thing that closes it, so there is
    # no `settled` bucket here. See the module docstring.
    for cid, projects in sorted(by_thread.items()):
        row = rows_by_id[cid]
        name = row.get("name") or "(unnamed)"
        label = f"{name} — goals: " + ", ".join(p for p, _n in projects)
        rows, problem = messages(cid)
        if problem:
            unreadable.append((label, cid, problem))
            continue
        newest = rows[-1]
        sender = str(newest.get("sender") or "").strip()
        age = _age_hours(newest.get("ts"), now)
        if not sender:
            unreadable.append((label, cid, "the newest message names no sender"))
        elif sender != SENDER:
            answered.append((label, cid, sender, age, str(newest.get("text") or "")))
        elif in_quiet_hours(_parse_ts(newest.get("ts"))):
            silenced.append((label, cid, age))
        else:
            waiting.append((label, cid, age))

    # Only once something is genuinely waiting, and only back to the oldest
    # thing that is waiting: a closed-out ask is not evidence he owes me a word.
    elsewhere = []
    open_ages = [age for _n, _c, age in waiting + silenced if age is not None]
    if open_ages:
        since = now - timedelta(hours=max(open_ages))
        hits, could_not_read = spoken_elsewhere(others, since, now)
        elsewhere = hits
        unreadable.extend(could_not_read)
    return answered, waiting, silenced, settled, unreadable, elsewhere, goals


def _age(hours):
    if hours is None:
        return "age unknown"
    if hours < 1:
        return f"{hours * 60:.0f} min ago"
    return f"{hours:.1f}h ago"


def _reconcile(goals, out):
    """Print what the goals document says, and return (clause, blind).

    The clause goes on every summary line, including the clean one, because
    the failure this exists to end is a summary that reads `0 waiting on him`
    while eleven projects are undecided. A count that is only printed when it
    is interesting is a count nobody can trust when it says nothing.

    `blind` is True when the document was there and could not be read, or
    when a project points at a thread this cannot resolve. Both are no
    instrument rather than no answer.
    """
    if goals is None:
        return "", False
    if goals.get("problem"):
        # The vault client is simply absent on the runner pod; say so and do
        # not raise, the same call `nas_health` makes.
        print(f"CANNOT SEE THE GOALS — {goals['problem']}", file=out)
        return " Goals not read, so nothing here reconciles against them.", False
    if goals.get("unreadable"):
        print(f"COULD NOT READ THE GOALS — {goals['unreadable']}", file=out)
        return " Goals unreadable.", True

    for project, written, pending in goals.get("unresolved", ()):
        print(f"CANNOT SEE THE THREAD — {project}", file=out)
        print(f"  its objective names {written}, which the active "
              f"conversation listing does not carry; {pending} key result(s) "
              "are still undecided and nothing here can say whether he has "
              "answered", file=out)
    for project, pending in goals.get("unargued", ()):
        print(f"ARGUED NOWHERE — {project}", file=out)
        print(f"  still discussing ({pending} key result(s)) and its "
              "objective names no conversation, so there is no thread for "
              "him to answer in. project_goals_check owns this one; opening "
              "the thread is the fix.", file=out)

    undecided = len(goals.get("rows", ()))
    judged = len(goals.get("by_thread", {}))
    clause = (f" Of {undecided} project(s) still discussing their goals, "
              f"{judged} thread(s) were judged above, "
              f"{len(goals.get('unargued', ()))} are argued nowhere and "
              f"{len(goals.get('unresolved', ()))} name a thread I cannot see.")
    return clause, bool(goals.get("unresolved"))


def report(answered, waiting, silenced, settled, unreadable, elsewhere=(),
           goals=None, out=sys.stdout, do_nudge=False, now=None):
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
    now = now or datetime.now(timezone.utc)
    audible = not in_quiet_hours(now)
    for name, cid, age in silenced:
        print(f"NEVER REACHED HIS PHONE — {name}", file=out)
        print(f"  {cid}  asked {_age(age)}, and the newest message landed in "
              "quiet hours, so Agora withheld the buzz and nothing retried it",
              file=out)
        if not do_nudge:
            print("  Re-announce it: python3 -m tools.ask_watch --nudge", file=out)
        elif not audible:
            print("  Not re-announced: it is quiet hours right now, so the "
                  "nudge would be withheld too. Run it after 07:00 Oslo.",
                  file=out)
        else:
            ok, detail = nudge(cid)
            print(f"  {'re-announced' if ok else 'COULD NOT re-announce'} — "
                  f"{detail}", file=out)
    for name, cid, age in waiting:
        print(f"waiting on him — {name}", file=out)
        print(f"  {cid}  asked {_age(age)}, he has not written in it", file=out)
    for name, cid in dict.fromkeys((n, c) for n, c, _w, _t in elsewhere):
        said = [(w, t) for n2, c2, w, t in elsewhere if c2 == cid]
        print(f"HE HAS BEEN TALKING ELSEWHERE — {name}", file=out)
        print(f"  {cid}  {OWNER} wrote there {len(said)} time(s) since the "
              f"oldest ask above was posted, last {_age(_age_hours(said[0][0], now))}",
              file=out)
        for when, text in said:
            print(f"    {' '.join(text.split())[:300]}", file=out)
        print("  Read that thread before writing that he is silent — the "
              "answer to an ask of mine has landed there before.", file=out)

    reconcile, blind = _reconcile(goals, out)

    if unreadable:
        print(f"Could not read {len(unreadable)} open ask(s) — that is no "
              "instrument, not no answer.", file=out)
        return 1
    if blind:
        return 1
    if elsewhere and not answered and not silenced:
        print(f"Nothing he answered is sitting unread in an ask thread, but he "
              f"has written {len(elsewhere)} time(s) elsewhere since the oldest "
              f"of {len(waiting)} open ask(s) — go and read those "
              f"threads.{reconcile}", file=out)
        return 2
    if answered or silenced:
        print(f"{len(answered)} of my open ask(s) have an answer nobody has "
              f"picked up; {len(silenced)} never reached his phone; "
              f"{len(waiting)} still waiting on him and "
              f"{len(settled)} answered and closed out. He has written "
              f"{len(elsewhere)} time(s) in another thread since the oldest "
              f"open ask.{reconcile}", file=out)
        return 2
    print(f"Nothing he answered is sitting unread; {len(waiting)} open ask(s) "
          f"still waiting on him and {len(settled)} answered and closed "
          f"out.{reconcile}", file=out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--nudge", action="store_true",
        help="re-announce every open ask whose push was withheld, so his "
             "phone finally buzzes for it")
    parser.add_argument(
        "--resolve", metavar="CONVERSATION_ID",
        help="close an ask he answered in some other thread: post why, then "
             "archive it so it stops reading as waiting. Needs --because.")
    parser.add_argument(
        "--goals", metavar="PATH", default=None,
        help="read project-goals.md from a local path instead of the vault, "
             "so this can be run from a pod with no vault client")
    parser.add_argument(
        "--because", metavar="TEXT", default="",
        help="one line for --resolve: where he answered and what it changed")
    args = parser.parse_args(argv)
    if args.resolve:
        rows, problem = messages(args.resolve)
        if problem:
            print(f"COULD NOT READ {args.resolve}: {problem}")
            return 1
        ok, detail = resolve(args.resolve, args.because, rows)
        print(f"{'resolved' if ok else 'NOT resolved'} — {detail}")
        return 0 if ok else 1
    markdown, problem, from_this_pod = read_goals(args.goals)
    return report(
        *check(goals_markdown=markdown,
               goals_problem=None if from_this_pod else problem,
               goals_unreadable=problem if from_this_pod else None),
        do_nudge=args.nudge)


if __name__ == "__main__":
    sys.exit(main())
