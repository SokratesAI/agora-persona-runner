"""Live tool-use chips for claude-cli personas, without handing the bridge
Agora's internal token.

Edvard asked for this twice in his own notes ("I can't see the tool usage
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
from agora_runner.config import TOOL_ACTIVITY_MAX_PER_CALL
from agora_runner.log import log


class _Grant:
    __slots__ = ("persona_name", "conversation_id", "count")

    def __init__(self, persona_name, conversation_id):
        self.persona_name = persona_name
        self.conversation_id = conversation_id
        self.count = 0


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


def report(token, capability, detail):
    """Post one chip. Returns False if the token is unknown or expired,
    which is the whole of this endpoint's authentication.

    Runs on an invoke_server handler thread while the generate() call that
    minted the token is still blocked on the bridge.
    """
    with _lock:
        entry = _grants.get(token)
        if entry is None:
            return False
        entry.count += 1
        count = entry.count
        persona_name = entry.persona_name
        conversation_id = entry.conversation_id

    if count > TOOL_ACTIVITY_MAX_PER_CALL:
        # One chip announcing the cap, then silence. Not a second-guess of
        # "All" -- it is a stop on a runaway loop posting thousands of
        # messages into one conversation on a 4-core box, and it says so in
        # the chat rather than quietly dropping calls the way the thing he
        # complained about did.
        if count == TOOL_ACTIVITY_MAX_PER_CALL + 1:
            log(f"tool activity cap hit for conversation {conversation_id} "
                f"after {TOOL_ACTIVITY_MAX_PER_CALL} chips")
            audit(persona_name, conversation_id, "tool activity",
                  f"capped at {TOOL_ACTIVITY_MAX_PER_CALL} chips for this run "
                  f"-- further tool calls are still running, just not shown",
                  ephemeral=True)
        return True

    # ephemeral: everything this module posts is narration, and Agora
    # retains narration on a budget separate from the capability audit
    # trail. Without it a single cycle's chips evict that trail wholesale.
    # Nothing is lost -- Agora also appends every chip to the conversation
    # itself, which is durable and is where these are actually read.
    audit(persona_name, conversation_id, capability, detail, ephemeral=True)
    return True
