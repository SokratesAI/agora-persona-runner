"""One authoritative cycle number, shared by Agora and the journal.

The owner, capture 2026-08-20, rated Immediately: *"There is a mossmaych
between the cycles displayed in Nova and the cycle name than ran in
agora. It is very confusing. They need to be the same number. The big is
that some cycles did not write their journals in the past."*

He is right about both halves. Two independent counters existed:

* Agora's conversation name, `<heartbeat> — Cycle N`, where N came from
  `len(existing_tagged_conversations) + 1` in `conversation_rotation`.
  It counts **runs**, and it increments whether or not the run produced
  anything.
* The journal, where `prompt.md` step 7 told each cycle to number itself
  "the previous highest plus one" -- read off the entries in
  `nova/journal/`. That counts **entries**.

A run that writes no entry advances one counter and not the other, and
nothing ever pulls them back together, so the gap is permanent and grows.
Measured 2026-08-20 07:53 Oslo: 277 tagged conversations exist, the newest
named `Nova — Cycle 277`, while the newest journal entry is Cycle 274 --
three runs that wrote nothing, three numbers of drift, and every journal
card on the owner's page mislabelled by three.

The fix is to stop deriving the number twice. The run count is the honest
one: it is a fact about what actually happened, it cannot go backwards,
and it does not depend on a cycle surviving long enough to write. So this
module owns it, `conversation_rotation` asks it for the next number, and
`prompt.md` step 7 tells a cycle to ask it for its own instead of counting
files. A run that writes no entry now leaves a **gap** in the journal
numbers rather than a silent shift -- honest, and already exactly what
`cycle_health.missing_cycles` was built to surface.

Note what deliberately does not change: the `<seq>-cycle-<n>.md` filename
prefix stays "previous highest plus one" and stays contiguous, because its
only job is to make a lexical sort chronological. The `<n>` is the part
that has to match Agora. And no historical entry is renumbered -- 326 of
them exist, hundreds of cross-references in prose name them by number, and
rewriting that is destructive in exchange for tidiness nobody asked for.
"""
import json
import re

from agora_runner.conversation_rotation import cycle_tag
from agora_runner.http_util import agora_get
from agora_runner.log import log
from agora_runner.vault import vault_read_path_rev, vault_write_path

# Matches nova_capture.py's WRITE_ATTEMPTS -- bounding the 409 retry is
# what stops two genuinely simultaneous claims for the same heartbeat from
# livelocking on one counter doc.
CLAIM_ATTEMPTS = 3

# One tiny counter doc per heartbeat, in Nova's own database (path prefix
# routes it there -- see vault.py's NOVA_DB_FOLDERS). Per-heartbeat rather
# than one shared doc: two *different* heartbeats claiming a number at the
# same moment should never contend with each other, only two claims for
# the *same* heartbeat legitimately need to serialize on the number itself.
_COUNTER_PATH = "projects/sokrates/projects/agora/nova/_cycle_counters/{}.json"

# Asked for in the conversation id's place when the caller genuinely has no
# id to give. A sentinel rather than an empty argument on purpose: an empty
# `$AGORA_CONVERSATION_ID` interpolates into an empty argument, so the two
# would be indistinguishable exactly when it matters -- see `main`.
NO_CONVERSATION_ID = "--no-conversation-id"

# `Nova — Cycle 277`. The separator is an em dash today and the heartbeat
# name is free text, so anchor on the number at the end and nothing else.
_NAME_RE = re.compile(r"Cycle\s+(\d+)\s*$")


def cycle_in_name(name):
    """The cycle number a conversation's name ends in, or None.

    The name is the only place a cycle number is written down -- the
    `evolve-cycle:` tag carries the *heartbeat* id, not the number -- so
    every reader that wants "which cycle is this thread" comes through
    here rather than keeping its own copy of `_NAME_RE`.
    """
    match = _NAME_RE.search(name or "")
    return int(match.group(1)) if match else None


def numbers_in(conversations, tag):
    """Every cycle number named by a conversation carrying `tag`, ascending.

    Parsing the names rather than counting the list is deliberate: a count
    silently goes backwards the day a conversation is deleted, and would
    then hand a fresh cycle a number an older one already used. A name that
    does not parse is skipped rather than guessed at.

    That is a future hazard, not a present one, and #250 claimed otherwise
    on a misread probe. Measured 2026-08-20 08:06 Oslo: 277 tagged
    conversations, 276 of them carrying a parseable number, highest 277, and
    the only number with no conversation is 1 -- the very first, named
    `Agora Evolve`, from before the naming convention existed. Counting and
    parsing both answer 278 today.
    """
    found = []
    for conversation in conversations or []:
        if tag not in (conversation.get("tags") or []):
            continue
        number = cycle_in_name(conversation.get("name") or "")
        if number is not None:
            found.append(number)
    return sorted(found)


def starts_in(conversations, tag):
    """`{cycle number: createdAt}` for every tagged conversation that names one.

    The heartbeat creates a cycle's conversation just before the session
    opens, so `createdAt` is when that cycle *woke* -- the one measured
    start time this system has. `nova_journal.with_start_times` puts it on
    the journal card, because the write time it used to show is the end of
    the run and the owner asked for the other end.

    **Earliest wins on a duplicate.** Two conversations can carry one
    number -- three did on 2026-08-24, before `current_number` learned to
    ask which one it was running in -- and the earliest is the honest
    answer to "when did work under this number begin". Picking by
    iteration order instead would make the card's time depend on how the
    API happened to sort, which is the kind of thing that looks stable for
    weeks and then is not.
    """
    out = {}
    for conversation in conversations or []:
        if tag not in (conversation.get("tags") or []):
            continue
        match = _NAME_RE.search(conversation.get("name") or "")
        created = conversation.get("createdAt")
        if not match or not created:
            continue
        number = int(match.group(1))
        if number not in out or created < out[number]:
            out[number] = created
    return out


def cycle_starts(heartbeat_id):
    """`starts_in` against a live Agora, or `{}` if it cannot be reached.

    `{}` rather than an exception, and rather than `None`: the only caller
    is the journal page, and a page that will not render because one
    timestamp source was unreachable is a worse answer than a page showing
    the write times it showed for its whole life until now.
    """
    try:
        status, listing = agora_get("/conversations")
    except Exception as e:
        log(f"cycle_starts: /conversations failed: {e}")
        return {}
    if status != 200:
        log(f"cycle_starts: /conversations returned {status}")
        return {}
    return starts_in(listing.get("conversations", []), cycle_tag(heartbeat_id))


def next_number(conversations, tag):
    """The number to give the conversation being created right now.

    Falls back to counting when no name parses at all -- that is the state
    on the very first rotation, and the state if the naming convention is
    ever changed out from under this, and in both `len + 1` is the answer
    the old code gave.
    """
    numbers = numbers_in(conversations, tag)
    if numbers:
        return numbers[-1] + 1
    tagged = [c for c in (conversations or []) if tag in (c.get("tags") or [])]
    return len(tagged) + 1


def claim_next_number(heartbeat_id, conversations, tag):
    """Atomically reserve the next cycle number for `heartbeat_id`.

    `next_number` alone is a read-then-write race: it answers from a
    snapshot of `/conversations` with nothing stopping two rotations that
    both read before either has created theirs from computing the same
    "next" number and both creating a conversation claiming it -- exactly
    the failure this module's own docstring already fixed once for a
    different cause (two counters instead of one). Concurrent rotations
    for the same heartbeat is a second way to reach the identical symptom.

    This wraps the same number in a real claim: a tiny per-heartbeat
    counter doc in Nova's own database, written with the read-modify-write
    + 409-retry idiom `nova_capture.set_priority` already uses for board
    edits. `conversations` still matters here as a floor, not a full
    replacement -- it is what seeds the counter correctly on its first
    ever use (before this existed, the honest answer was still "the
    highest number that exists"), and it protects against drift if the
    counter doc and Agora's conversation list ever disagree (a
    conversation deleted by hand, a counter doc that predates a cycle it
    should account for).

    Falls back to the bare `next_number` scan if the counter can't be read
    or written after retrying -- rotate_cycle_conversation's own rule is
    that a rotation bug must never be the reason a real cycle doesn't run,
    and a duplicate number once in a while under a CouchDB outage is a far
    smaller cost than a heartbeat going silent.
    """
    path = _COUNTER_PATH.format(heartbeat_id)
    floor = next_number(conversations, tag) - 1

    for _ in range(CLAIM_ATTEMPTS):
        try:
            existing, rev = vault_read_path_rev(path)
        except Exception as exc:
            log(f"claim_next_number({heartbeat_id}): counter read failed, "
                f"falling back to scan: {type(exc).__name__}: {exc}")
            break
        stored = 0
        if existing:
            try:
                stored = int(json.loads(existing).get("n", 0))
            except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
                stored = 0
        claim = max(stored, floor) + 1
        result = vault_write_path(path, json.dumps({"n": claim}), if_rev=rev)
        if result == "written":
            return claim
        if "409" not in result:
            log(f"claim_next_number({heartbeat_id}): counter write failed, "
                f"falling back to scan: {result}")
            break
    else:
        # Every attempt lost a 409. Both `break`s above say why they gave
        # up; this path said nothing, and it is the one where the fallback
        # is most likely to hand back a number somebody else already holds
        # -- CLAIM_ATTEMPTS conflicts in a row means real contention, not a
        # sick CouchDB. A duplicate cycle number is exactly the symptom
        # the owner reported as Immediately on 2026-08-20, so the one case
        # that can still produce one should not be the one that leaves no
        # trace to find it by.
        log(f"claim_next_number({heartbeat_id}): {CLAIM_ATTEMPTS} conflicts "
            f"in a row, falling back to scan -- cycle {floor + 1} may collide")
    return floor + 1


def number_of(conversations, conversation_id):
    """The cycle number named by one specific conversation, or `None`.

    `None` covers both "no conversation has that id" and "it has one and the
    name does not parse", because the caller does the same thing with either:
    say so, and fall back. Splitting them would buy a message nobody reads.
    """
    for conversation in conversations or []:
        if conversation.get("id") != conversation_id:
            continue
        match = _NAME_RE.search(conversation.get("name") or "")
        return int(match.group(1)) if match else None
    return None


def current_number(heartbeat_id, conversation_id=None):
    """The number of the cycle running *now*, or `None` if it can't be read.

    This is what a live cycle calls to find out what to call itself.

    **`conversation_id` is which conversation the caller is actually running
    in, and without it this function cannot answer the question it is asked.**
    It used to take the highest number that existed, on the reasoning that
    `rotate_cycle_conversation` creates a cycle's conversation just before the
    cycle wakes -- true, and sufficient, for exactly as long as one cycle ran
    at a time. Cycles overlap now. On 2026-08-24 three of them were alive
    together, each asked for "the highest", each got the newest cycle's
    number, and all three wrote a journal entry headed `Cycle 380`. The owner
    found it from the outside: comments are keyed by cycle number
    (`nova_comments`), so his reply to one card was answered from another, and
    number 379 is missing from the journal for good. The bridge exports the id
    as `AGORA_CONVERSATION_ID` (agora-claude-bridge#72).

    Called without one, the old highest-wins answer is what you get, because
    that is still right whenever only one cycle is running and it is the only
    answer available to a caller that has no id. `main` says out loud which of
    the two it used, so a duplicate number is never silent again.

    `None` rather than a guess when Agora cannot be reached: a cycle that
    cannot read its number must be told so, not handed a plausible one that
    quietly reintroduces the drift this module exists to remove.
    """
    return current_number_with_source(heartbeat_id, conversation_id)[0]


def current_number_with_source(heartbeat_id, conversation_id=None):
    """`(number, source)` -- the same number, plus how it was actually derived.

    `source` is one of:

    * `"conversation"` -- the given id named a conversation and this is its
      number. The only source a live cycle should ever act on.
    * `"highest"` -- no id was given, so this is the highest number that
      exists. Right whenever one cycle is running, and `main` says so.
    * `"unknown-conversation"` -- **an id was given and Agora knows no
      conversation by it**, so the number beside it answers a different
      question than the one the caller asked.
    * `"unreachable"` -- Agora did not answer; the number is `None`.

    The fourth case exists because the third one was silent for three weeks
    and cost a journal entry. `current_number`'s own docstring said the
    fallback was safe because *"`main` says out loud which of the two it
    used"*, and the test that pinned the fallback said the same thing in its
    docstring -- but `main` printed its warning only when no id was given at
    all, so the one caller who *had* an id and got it wrong was the one told
    nothing. Measured on cycle 1183 (2026-09-07): it ran
    `cycle_number <heartbeat-id> 08ffac94-7c4a-4506-897f-968c592358cb`,
    passing Nova's **personaId** -- the id the session prompt hands every
    cycle for `create_heartbeat` -- where the conversation id belongs. No
    conversation carries that id, so it fell through to the highest, which at
    22:48:38Z was 1184 because cycle 1184's conversation had been created six
    minutes earlier. Cycle 1183 then filed its entry as `cycle-1184.md`, and
    two things went missing at once: `cycle_postmortem` reports 1183 as
    *"ran and left no record"* when the record exists one number up, and
    cycle 1184 -- which really did run 28m and write nothing -- is reported
    as fine, because a file with its name is sitting there.

    So the distinction is kept here rather than collapsed into `None`: the
    caller that passed an id needs to know its id was not used, and that is
    a different instruction ("check the id") from an unreachable Agora
    ("retry, then fall back"). `current_number` keeps returning the plausible
    number for every other caller, because a library that hands back `None`
    on a bad id would make a rotation bug the reason a cycle does not run.
    """
    try:
        status, listing = agora_get("/conversations")
    except Exception:
        return None, "unreachable"
    if status != 200:
        return None, "unreachable"
    conversations = listing.get("conversations", [])
    numbers = numbers_in(conversations, cycle_tag(heartbeat_id))
    highest = numbers[-1] if numbers else None
    if conversation_id:
        mine = number_of(conversations, conversation_id)
        if mine is not None:
            return mine, "conversation"
        return highest, "unknown-conversation"
    return highest, "highest"


def main():
    """`cd /app && python3 -m agora_runner.cycle_number <heartbeat-id> <conversation-id>`.

    In `agora_runner/` and not `tools/` on purpose: `tools/` is not copied
    into the container image, and the shell a cycle has inside the runner
    pod (`terminal_exec`) only has `/app`. The bridge pod, which is where
    `Bash` runs, has the checkout but no route to Agora.

    The conversation id is a second argument rather than an environment
    variable because the two shells a cycle has are two different pods: the
    bridge exports `AGORA_CONVERSATION_ID` into the *CLI's* environment, and
    this runs in the runner pod, which never sees it. So the cycle reads it in
    one shell and passes it to the other. `prompt.md` step 7 has the block.

    Only `stdout` carries the number; everything about *how* it was derived
    goes to `stderr`, so `$(...)` around this stays exactly one integer.

    Exit 0 with the number, 1 if Agora could not be read, 2 on bad arguments,
    **3 if a conversation id was given that names no conversation**, and **4
    if no conversation id was given at all** -- see
    `current_number_with_source` for the cycle that cost.

    **Exit 4 is the other half of exit 3, and it is the half that actually
    happened 39 times.** A wrong id has refused since #907; a *missing* one
    still printed the highest number that exists with a warning on stderr,
    and `cycle_postmortem.misfiled_entries` measured what that costs --
    cycles 1183 through 1204 each filed their entry one number up, because a
    run that outlives the heartbeat interval is handed the number of the
    cycle that woke after it. The warning was there the whole time. It went
    to `stderr`, which is exactly the stream a `$(...)` capture drops on the
    floor, and stdout still carried a plausible integer, so nothing
    downstream could tell the two apart. A refusal can be: it puts nothing
    on stdout, which is the same shape as exit 3 and is what makes the
    caller stop.

    The fallback is not removed, because an old bridge really can have no id
    to pass (`AGORA_CONVERSATION_ID` arrived in agora-claude-bridge#72), and
    a rotation bug must never be the reason a cycle does not run. It is made
    **explicit**: `--no-conversation-id` in the id's place asks for the
    highest number on purpose, warns exactly as before, and exits 0. So the
    deliberate case still works and the accidental one -- including an empty
    `$AGORA_CONVERSATION_ID` interpolated into the command line, which is the
    accident this is -- now stops instead of answering.
    """
    import sys

    if len(sys.argv) not in (2, 3):
        print("usage: python3 -m agora_runner.cycle_number <heartbeat-id> "
              "<conversation-id>|--no-conversation-id",
              file=sys.stderr)
        return 2
    given = sys.argv[2] if len(sys.argv) == 3 else ""
    deliberately_without = given == NO_CONVERSATION_ID
    conversation_id = "" if deliberately_without else given
    if not conversation_id and not deliberately_without:
        # Nothing on stdout, for exit 3's reason: the caller believed it was
        # asking "which cycle am I", and the highest number answers "which
        # cycle woke most recently", which is a different question whenever
        # more than one cycle is alive.
        print("no conversation id given, so the cycle number cannot be derived from "
              "the conversation this cycle is running in. Read $AGORA_CONVERSATION_ID "
              "in the bridge shell and pass it. Pass "
              f"{NO_CONVERSATION_ID} instead only if the bridge exports none -- that "
              "asks for the highest number that exists, which collides whenever "
              "cycles overlap.",
              file=sys.stderr)
        return 4
    number, source = current_number_with_source(sys.argv[1], conversation_id or None)
    if source == "unknown-conversation":
        # Refused rather than warned, and nothing on stdout. The caller passed
        # an id because it believed it had one; the highest number is an answer
        # to a question it did not ask, and a cycle writes its number into a
        # document that is never edited afterwards. Retrying does not help
        # here, which is why this is not exit 1 -- a different id does.
        print(f"'{conversation_id}' names no conversation Agora knows, so the cycle "
              "number was not derived from it. Check $AGORA_CONVERSATION_ID in the "
              "bridge shell -- a persona id is not a conversation id.",
              file=sys.stderr)
        return 3
    if number is None:
        print("could not read the cycle number from Agora", file=sys.stderr)
        return 1
    if source == "highest":
        print("warning: no conversation id given, so this is the highest number that "
              "exists, not necessarily yours -- concurrent cycles will collide on it",
              file=sys.stderr)
    print(number)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
