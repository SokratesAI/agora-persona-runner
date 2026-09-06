"""An ask he answered in chat, rather than on the journal card.

His issue #165 has two halves. The first is built: the yellow "N WAITING ON
YOU" panel is gone and `/asks` is the journal feed with one filter on it.
The second is this, in his own words -- *"decisions [the owner] makes live
in chat (not through a journal comment) currently only get written to
notes.md, which nothing turns back into an actual 'resolved' state on the
relevant journal entry/board row -- so 'waiting on you' badges keep showing
even after he's answered in conversation."*

The card is not where the question reached him. A cycle's ask goes out in
the reply at the end of the run, which is push-notified to his phone, and
the natural place to answer a push notification is the thread it arrived
in. Answering there left no trace anywhere the site looked, so the ask sat
on `/asks` until he went and repeated himself in a comment box.

**A cycle's thread is one conversation and he speaks in it or he does
not.** That is the whole test here: a message in cycle N's thread whose
sender is the owner and which is not narration means cycle N's ask has an
answer. There is no window and no timestamp comparison -- the thread exists
only for that one run, so anything he says in it is about that run.

Two things this deliberately does not do.

It does not read the answer, or decide whether it settles the question. He
may well say "not now" -- that is still an answer, and the badge is a
prompt for him, not a record for me. A cycle reading his words and writing
`Resolves the ask from cycle N` into its own entry is the mechanism that
records the outcome, and `nova_journal.resolved_asks` already carries it.

It does not touch the board. His issue names journal entries and board rows
in one sentence, and a board row is answered by a comment on the row, which
is a different document with its own unanswered flag (`tools.top_board_rows`).
Folding both into one predicate would mean a chat message clearing a row he
has never seen.

Nothing here does I/O. It takes a conversation listing and a callable that
reads one thread, so the site can cache the reads and a test can hand it a
dict.
"""

from agora_runner.cycle_number import cycle_in_name
from agora_runner.nova_conversations import OWNER_SENDER
from agora_runner.log import log


def spoke(thread):
    """True when the owner said something in this thread.

    `partial` is narration -- the tool chips and the passages a cycle
    streams while it works -- and it is never his; the flag is checked
    anyway because it is the same "a message that is not narration" rule
    `reply_check.replied` applies from the other side, and a future sender
    field on a steps-only row must not read as him.
    """
    for message in (thread or {}).get("messages") or []:
        if message.get("partial"):
            continue
        if message.get("sender") == OWNER_SENDER:
            return True
    return False


def cycle_threads(listing):
    """`{cycle number: [conversation id, ...]}` for the cycle threads.

    A list per cycle rather than one id: six cycles have written two
    entries, and two conversations can name one number. He only has to have
    spoken in one of them.

    `cycleThread` is the site's own flag for a thread a heartbeat opened, so
    a conversation he started himself is not a cycle's thread even if he
    named it after one.
    """
    found = {}
    for row in (listing or {}).get("conversations") or []:
        if not row.get("cycleThread"):
            continue
        number = cycle_in_name(row.get("name") or "")
        if number is None or not row.get("id"):
            continue
        found.setdefault(number, []).append(row["id"])
    return found


def answered_in_chat(cycles, listing, read_thread):
    """Which of `cycles` he has spoken in, ascending.

    `read_thread` takes a conversation id and returns what
    `nova_conversations.thread` returns. It is passed in so the caller owns
    the fetching -- this is one Agora call per open ask and the site caches
    the answer.

    **A thread that cannot be read is not an answer.** The one failure this
    has to get right is which way to be wrong: showing him a question he has
    already answered costs a scroll, hiding one he has not costs him the
    question. So a fetch that raises is logged and the cycle stays open,
    which is the same direction `app.js` takes when the comments payload
    fails.
    """
    threads = cycle_threads(listing)
    answered = []
    for cycle in sorted(set(cycles)):
        for conversation_id in threads.get(cycle, []):
            try:
                thread = read_thread(conversation_id)
            except Exception as e:  # noqa: BLE001 -- see docstring
                log(f"nova_chat_answers: thread {conversation_id} unreadable: {e}")
                continue
            if spoke(thread):
                answered.append(cycle)
                break
    return answered
