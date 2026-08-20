"""One authoritative cycle number, shared by Agora and the journal.

Edvard, capture 2026-08-20, rated Immediately: *"There is a mossmaych
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
card on Edvard's page mislabelled by three.

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
import re

from agora_runner.conversation_rotation import cycle_tag
from agora_runner.http_util import agora_get

# `Nova — Cycle 277`. The separator is an em dash today and the heartbeat
# name is free text, so anchor on the number at the end and nothing else.
_NAME_RE = re.compile(r"Cycle\s+(\d+)\s*$")


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
        match = _NAME_RE.search(conversation.get("name") or "")
        if match:
            found.append(int(match.group(1)))
    return sorted(found)


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


def current_number(heartbeat_id):
    """The number of the cycle running *now*, or `None` if it can't be read.

    This is what a live cycle calls to find out what to call itself. The
    conversation it is running in was created by `rotate_cycle_conversation`
    before it woke, so the highest number that exists is its own.

    `None` rather than a guess: a cycle that cannot reach Agora must be told
    so, not handed a plausible number that quietly reintroduces the drift
    this module exists to remove.
    """
    try:
        status, listing = agora_get("/conversations")
    except Exception:
        return None
    if status != 200:
        return None
    numbers = numbers_in(listing.get("conversations", []), cycle_tag(heartbeat_id))
    return numbers[-1] if numbers else None


def main():
    """`cd /app && python3 -m agora_runner.cycle_number <heartbeat-id>`.

    In `agora_runner/` and not `tools/` on purpose: `tools/` is not copied
    into the container image, and the shell a cycle has inside the runner
    pod (`terminal_exec`) only has `/app`. The bridge pod, which is where
    `Bash` runs, has the checkout but no route to Agora.
    """
    import sys

    if len(sys.argv) != 2:
        print("usage: python3 -m agora_runner.cycle_number <heartbeat-id>", file=sys.stderr)
        return 2
    number = current_number(sys.argv[1])
    if number is None:
        print("could not read the cycle number from Agora", file=sys.stderr)
        return 1
    print(number)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
