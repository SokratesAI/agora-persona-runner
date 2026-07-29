"""Persona/workflow lookups against Agora's own API, with a per-tick cache."""

from agora_runner.http_util import agora_get, agora_internal


_persona_cache = {}


def clear_persona_cache():
    """Called once per poll_once() tick -- the cache is per-tick, not persistent."""
    _persona_cache.clear()


def fetch_persona(persona_id):
    if persona_id in _persona_cache:
        return _persona_cache[persona_id]
    status, body = agora_internal("GET", f"/personas/{persona_id}")
    persona = body.get("persona") if status == 200 else None
    _persona_cache[persona_id] = persona
    return persona


def fetch_persona_uncached(persona_id):
    """Decisions/0009 — workflow runs execute on their own thread (see
    run_due_heartbeats), concurrently with the main poll loop clearing
    _persona_cache every tick. A workflow thread must never touch that
    shared dict; one extra HTTP GET per round is noise next to an LLM
    call anyway."""
    status, body = agora_internal("GET", f"/personas/{persona_id}")
    return body.get("persona") if status == 200 else None


def fetch_workflow(workflow_id):
    status, body = agora_get(f"/workflows/{workflow_id}")
    return body.get("workflow") if status == 200 else None
