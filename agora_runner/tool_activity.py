"""Live tool-use chips for claude-cli personas, without handing the bridge
Agora's internal token.

The owner asked for this twice in his own notes ("I can't see the tool usage
of Claude cli agents in Agora. I want that"), and when asked whether he
wanted every call or only the ones that change something, answered
2026-08-03: "All. I want to know whats going on. It takes away my feeling
of control if everything is hidden."

Every other provider runs its tools inside this process, so audit() is a
plain function call on the same stack. A claude-cli persona's tools run
inside the Claude Code CLI, in a different pod (agora-claude-bridge), and
this process learns nothing at all until that whole session returns --
which for an Evolve cycle is up to 45 minutes. Reporting the calls
afterwards is not an option: that is exactly the "displayed after the
process is finished... they serve no purpose other than hindsight logging"
complaint that #37 just fixed for heartbeat chips.

So the bridge has to push each call as it happens, and the only write path
that renders a chip is Agora's INTERNAL /audit, gated by AGORA_TOKEN -- a
secret the bridge pod's ServiceAccount deliberately cannot read (it has no
`get secrets` in `agents` at all). Passing that token over the wire would
give a pod running an unrestricted shell the keys to Agora's internal API
to save one network hop. This module is the hop instead: the runner mints
a random single-purpose token per generate() call, hands the bridge that
plus a callback URL, and accepts reports bearing it -- for one
conversation, for the lifetime of that one call, for nothing but posting
an activity chip. The bridge never learns AGORA_TOKEN and cannot address
any conversation but the one it is already generating for.
"""

import secrets
import threading

from agora_runner.audit import audit


class _Grant:
    __slots__ = ("persona_name", "conversation_id")

    def __init__(self, persona_name, conversation_id):
        self.persona_name = persona_name
        self.conversation_id = conversation_id


# Guards _grants only. audit() (a blocking HTTP POST) is deliberately
# called outside the lock: reports arrive on invoke_server's handler
# threads, and holding the lock across a network call would serialize
# every persona's chips behind the slowest one.
_lock = threading.Lock()
_grants = {}


def grant(persona_name, conversation_id):
    """Authorise activity chips for one conversation, returning the token.

    Returns None when there is no conversation to post into (the /invoke
    path builds a reply with conversation_id=None) -- there is no chip to
    render, so there is no reason to have the bridge report at all.
    """
    if not conversation_id:
        return None
    token = secrets.token_urlsafe(32)
    with _lock:
        _grants[token] = _Grant(persona_name, conversation_id)
    return token


def revoke(token):
    """End a grant. Called in a finally, so a failed generate() cannot
    leave a live token behind for the rest of the process's life."""
    if token:
        with _lock:
            _grants.pop(token, None)


def report(token, capability, detail, tool_use_id="", output=None, is_error=False,
           retracted=False):
    """Post one chip. Returns False if the token is unknown or expired,
    which is the whole of this endpoint's authentication.

    Runs on an invoke_server handler thread while the generate() call that
    minted the token is still blocked on the bridge.

    A call is narrated twice: once when it starts (detail, no output) and
    once when it returns (output, no detail), both under the same
    tool_use_id. Two posts rather than one amended post, because the first
    is already on the owner's screen by the time the tool finishes -- a
    `pytest` run takes minutes and he asked to see it start, not to see it
    appear already-complete afterwards. Agora's client pairs them by
    tool_use_id and renders one chip (public/app.js). If one half is lost,
    the other still stands alone, which is why nothing here waits for a pair.
    """
    with _lock:
        entry = _grants.get(token)
        if entry is None:
            return False
        persona_name = entry.persona_name
        conversation_id = entry.conversation_id

    # There is deliberately no ceiling here. There was one -- 400 chips per
    # call, after which this went silent -- and the owner struck it down on
    # 2026-08-04: "limiting the tool calls (which limits your ability) just
    # because you think it will improve the ui is against everything we stand
    # for... If a cap is needed because of a buffer overflow or something
    # more dangerous I completely understand."
    #
    # It wasn't. Measured on this box, an append to a conversation costs 6ms
    # at 400 messages, so a whole cycle's narration is ~1s of I/O and under a
    # megabyte -- the cap was guarding a cost that does not exist at the
    # volume anything here actually runs at. It only became real at ~10k
    # messages (50ms/append, 10GB written for one run), and that is a
    # property of MessageStore.persist rewriting the entire conversation file
    # on every append, which affects every chatty persona and is not fixed by
    # silencing one of them. It's written down in issues.md as its own
    # problem instead.
    #
    # The volume question the cap was really about is answered in the UI now:
    # narration collapses into a drawer that is hidden by default (agora#38),
    # so all of it is kept and none of it is in the way.

    # ephemeral: everything this module posts is narration, and Agora
    # retains narration on a budget separate from the capability audit
    # trail. Without it a single cycle's chips evict that trail wholesale.
    # Nothing is lost -- Agora also appends every chip to the conversation
    # itself, which is durable and is where these are actually read.
    audit(persona_name, conversation_id, capability, detail, ephemeral=True,
          tool_use_id=tool_use_id, output=output, is_error=is_error,
          retracted=retracted)
    return True
