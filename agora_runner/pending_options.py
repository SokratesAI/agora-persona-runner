"""Answers a persona offers the owner as buttons, held until its turn's reply posts.

Idea #164, slice 3. Agora stores `options` on a message (agora#98) and the
Nova app draws one button per option -- but only on the newest message in
the thread (runner#1247), and a tap sends the label back as an ordinary
message from him. So a tool that posted the question as a message of its own
would be answered by nothing: the turn's own reply lands after it, the
question is no longer the newest message, and its buttons go grey before he
has seen them.

So `ask_edvard` posts nothing. It records the options here, keyed by
conversation, and the code that posts the turn's reply (`conversations.speak`
for a chat turn, `heartbeats.run_heartbeat` for a scheduled one) takes them
and attaches them to that reply -- the last message the turn writes. The
question the buttons answer is the reply itself, which is why the tool tells
the model to end its reply on the question.

The tap arriving while the turn is still running is not a case to handle:
the buttons are on the reply, and the reply posts when the turn ends.

Tool calls arrive on invoke_server's handler threads and the reply is posted
from the turn's own thread, hence the lock.
"""
import threading

_lock = threading.Lock()
_pending = {}


def check(options):
    """The same shape Agora's `checkMessageOptions` enforces -- 2 to 6 answers,
    each one line of 1 to 80 characters, no repeats -- checked here so a bad
    list fails the tool call the model can see and correct, rather than the
    notify at the end of the turn, where Agora would 400 the whole reply.
    Returns the error, or None."""
    if not isinstance(options, list) or not 2 <= len(options) <= 6:
        return "options must be a list of 2 to 6 answers"
    for option in options:
        if (not isinstance(option, str) or not option.strip() or len(option) > 80
                or "\n" in option or "\r" in option):
            return "each option must be one line of 1 to 80 characters"
    if len(set(options)) != len(options):
        return "options must not repeat"
    return None


def offer(conversation_id, options):
    """Hold `options` for this conversation's next reply. A second call in the
    same turn replaces the first: one reply carries one set of buttons."""
    error = check(options)
    if error:
        return error
    with _lock:
        _pending[conversation_id] = list(options)
    return None


def take(conversation_id):
    """The options held for this conversation, removed; None when there are none."""
    with _lock:
        return _pending.pop(conversation_id, None)


def clear(conversation_id):
    """Drop anything held -- called as a turn starts, so options from a turn
    that failed before posting can never land on a later, unrelated reply."""
    take(conversation_id)
