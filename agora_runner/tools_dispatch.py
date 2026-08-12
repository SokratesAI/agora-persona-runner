"""execute_tool -- the single dispatch point every provider calls for every tool_use block."""

import json
from collections import OrderedDict

from agora_runner.log import debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.audit import audit
from agora_runner.vault import (
    vault_read_path, vault_read_path_rev, vault_write_path, vault_append_path,
    vault_list_prefix, vault_search,
    VaultIncompleteDocument,
    vault_query_frontmatter, vault_validate_frontmatter_schema, vault_find_stub_notes,
    vault_find_duplicate_titles, vault_get_token_metrics, vault_git_revision_history,
    vault_summarize_recent_agent_work, vault_update_frontmatter_batch,
    unreadable_note,
)
from agora_runner.tools_kubectl import kubectl_read
from agora_runner.tools_github import github_read, create_pr, github_comment, merge_pr
from agora_runner.tools_terminal import terminal_exec
from agora_runner.tools_search import web_search_tinyfish
from agora_runner.nova_capture import capture as capture_to_backlog


def _resolve_scoped_target(active_step, args):
    """Decisions/0009 — resolves scoped_write's real target path from
    the step's own filepath, never from model-supplied args. An exact
    file (no trailing '/') always resolves to itself. A folder (trailing
    '/') resolves to whatever filename the FIRST call this step names
    (validated to stay inside the folder — no '..'/absolute escape),
    then locked onto active_step for every later call this step.
    Returns None only for the "folder, no filename given yet" case —
    callers should surface that as a clear tool error, not a write."""
    filepath = active_step.get("filepath", "")
    if not filepath.endswith("/"):
        return filepath
    locked = active_step.get("_locked_path")
    if locked is not None:
        return locked
    filename = str(args.get("filename", "")).strip()
    if not filename or filename.startswith("/") or ".." in filename:
        return None
    locked = filepath + filename
    active_step["_locked_path"] = locked
    return locked


#: What revision each conversation last *read* a vault path at, so the
#: write that follows can be conditional on it. Keyed
#: (conversation_id, lowercased path) -> rev, where None is a real and
#: different expectation: "there was no document here when I looked".
#:
#: A persona's edit is a read-modify-write spread across turns -- it calls
#: vault_read, reasons for a while, then calls vault_write minutes later.
#: The whole window between those two calls is where somebody else's write
#: gets destroyed, so the revision that has to travel is the one from the
#: read, not one fetched just before the PUT. Grabbing a fresh revision at
#: write time is what vault_write_path already did on its own, and it is
#: exactly the silent clobber `if_rev` was added to stop (vault.py,
#: `vault_read_path_rev`).
_READ_REVS = OrderedDict()

#: The runner process lives for days and serves every persona and every
#: conversation, so this dict has no natural end -- that is the danger, and
#: it is the only reason there is a number here. Eviction cannot break a
#: write: forgetting a revision downgrades that one write to the
#: unconditional behaviour it had before this existed, which is a lost
#: protection, never a lost or spuriously rejected write.
_READ_REVS_MAX = 512

#: "This conversation never read this path", as distinct from the None that
#: `vault_read_path_rev` returns for a path holding no document.
_NO_READ = object()


def _remember_read_rev(conversation_id, path, rev):
    key = (conversation_id, path.lower())
    _READ_REVS.pop(key, None)
    _READ_REVS[key] = rev
    while len(_READ_REVS) > _READ_REVS_MAX:
        _READ_REVS.popitem(last=False)


def _claim_read_rev(conversation_id, path):
    """The revision this conversation read `path` at, and forget it.

    Forgetting is the point of `pop`. After the write lands, the remembered
    revision is stale by definition, and a second write with no read in
    between would fail against it forever. Consuming it means that write is
    unconditional -- no worse than before this change -- while the write
    that actually follows a read is protected.

    Returns `_NO_READ` (not None) when this conversation never read the
    path, because None means "I read it and there was nothing there".
    """
    return _READ_REVS.pop((conversation_id, path.lower()), _NO_READ)


def _conditional_write(conversation_id, path, content):
    """vault_write_path, conditional on the read this conversation did."""
    rev = _claim_read_rev(conversation_id, path)
    if rev is _NO_READ:
        return vault_write_path(path, content)
    result = vault_write_path(path, content, if_rev=rev)
    if "409 conflict" in result:
        # The caller here is a model, and it can act on this: it has the
        # tool it needs to recover, and no way to guess that from a bare
        # 409. Retrying the write alone would resend the body built from
        # the text it lost the race to -- the clobber, spelled out.
        return (f"{result} -- read it again with vault_read, re-apply your "
                f"change to what is there now, then write again")
    return result


def _before_snapshot(path):
    """The pre-write content, for the audit diff only -- never a gate.

    `vault_read_path` raises VaultIncompleteDocument when a file's content
    chunks are partly missing from CouchDB. Letting that escape here would
    block `vault_write`, and a full overwrite is exactly how a damaged file
    gets *repaired* -- so the audit read, which this function's callers have
    always treated as best effort, would have become the one thing standing
    between a persona and the fix. `vault_append` is unaffected: it is
    blocked by `vault_append_path`'s own read, which is the read that
    actually matters, because appending onto a truncated file is what makes
    the truncation permanent.
    """
    try:
        return vault_read_path(path) or ""
    except VaultIncompleteDocument as e:
        return f"[unreadable before this write: {e}]"


def _audit_vault_write(persona_name, conversation_id, capability, path, result, before, after):
    """Record what the write actually did, not what it was asked to do.

    Every vault write path returns either "written" or a "FAILED(...)"
    string -- the file didn't exist, CouchDB rejected the PUT, or (since
    #35) an after_marker matched no line. The three call sites below used
    to pass before/after unconditionally, so a call that wrote nothing
    still produced an audit entry carrying the new content as the "after"
    side. Agora's Activity feed renders that as a completed diff.

    That matters more than it sounds: the audit log is the only durable
    record of what a persona did to Edvard's vault, and it was lying in
    precisely the cases worth reviewing. #35 made the FAILED path
    genuinely reachable for vault_append, which is why this is being
    fixed now. On failure the attempt is still audited -- that a persona
    tried to write is real -- but the reason goes in the detail and no
    diff is claimed."""
    if str(result).startswith("FAILED"):
        audit(persona_name, conversation_id, capability, f"{path} — {result}")
        return
    audit(persona_name, conversation_id, capability, path, before=before, after=after)


def execute_tool(name, args, persona, conversation_id, active_step=None):
    persona_name = persona.get("name", "?")
    debug_log(f"execute_tool: {name} args={json.dumps(args)[:200]} persona={persona_name} conversation={conversation_id}")
    try:
        if name == "web_search":
            query = str(args.get("query", ""))
            audit(persona_name, conversation_id, "web_search", query)
            return web_search_tinyfish(query)
        if name == "vault_read":
            path = str(args.get("path", ""))
            audit(persona_name, conversation_id, "vault_read", path)
            content, rev = vault_read_path_rev(path)
            # Remember the revision even when nothing was there: a write
            # that follows then says "this file should still not exist",
            # which is what stops two personas both creating it and one
            # of them disappearing.
            _remember_read_rev(conversation_id, path, rev)
            return content if content is not None else f"[not found: {path}]"
        if name == "vault_list":
            prefix = str(args.get("prefix", ""))
            audit(persona_name, conversation_id, "vault_list", prefix)
            paths = vault_list_prefix(prefix)
            note = unreadable_note(paths, "vault_list")
            return note + ("\n".join(paths[:200]) or "[no files under that prefix]")
        if name == "vault_write":
            path = str(args.get("path", ""))
            content = str(args.get("content", ""))
            # Read before overwriting so the audit entry can carry a
            # real before/after pair for the Activity diff view. Best
            # effort -- a failed read (e.g. new file) just means "" as
            # the before side, same as the file not existing.
            before = _before_snapshot(path)
            result = _conditional_write(conversation_id, path, content)
            _audit_vault_write(persona_name, conversation_id, "vault_write", path, result, before, content)
            return result
        if name == "vault_append":
            path = str(args.get("path", ""))
            content = str(args.get("content", ""))
            after_marker = str(args.get("after_marker", ""))
            before = _before_snapshot(path)
            result = vault_append_path(path, content, after_marker)
            _audit_vault_write(persona_name, conversation_id, "vault_append", path, result, before, content)
            return result
        if name == "vault_search":
            query = str(args.get("query", ""))
            audit(persona_name, conversation_id, "vault_search", query)
            return vault_search(query, str(args.get("prefix", "")), int(args.get("max_results") or 20))
        if name == "vault_query_frontmatter":
            field = str(args.get("field", ""))
            audit(persona_name, conversation_id, "vault_query_frontmatter", field)
            return vault_query_frontmatter(field, str(args.get("value", "")), str(args.get("prefix", "")))
        if name == "vault_validate_frontmatter_schema":
            prefix = str(args.get("prefix", ""))
            audit(persona_name, conversation_id, "vault_validate_frontmatter_schema", prefix)
            return vault_validate_frontmatter_schema(prefix)
        if name == "vault_find_stub_notes":
            prefix = str(args.get("prefix", ""))
            audit(persona_name, conversation_id, "vault_find_stub_notes", prefix)
            return vault_find_stub_notes(prefix, int(args.get("min_chars") or 40))
        if name == "vault_find_duplicate_titles":
            prefix = str(args.get("prefix", ""))
            audit(persona_name, conversation_id, "vault_find_duplicate_titles", prefix)
            return vault_find_duplicate_titles(prefix)
        if name == "vault_get_token_metrics":
            prefix = str(args.get("prefix", ""))
            audit(persona_name, conversation_id, "vault_get_token_metrics", prefix)
            return vault_get_token_metrics(prefix)
        if name == "vault_git_revision_history":
            detail = f"path={args.get('path', '?')} sha={args.get('sha', '?')}"
            audit(persona_name, conversation_id, "vault_git_revision_history", detail)
            return vault_git_revision_history(
                str(args.get("path", "")), int(args.get("limit") or 10), str(args.get("sha", ""))
            )
        if name == "vault_summarize_recent_agent_work":
            hours = int(args.get("hours") or 24)
            audit(persona_name, conversation_id, "vault_summarize_recent_agent_work", f"{hours}h")
            return vault_summarize_recent_agent_work(hours)
        if name == "vault_update_frontmatter_batch":
            field = str(args.get("field", ""))
            value = str(args.get("value", ""))
            detail = f"{field}={value} prefix={args.get('prefix', '')}"
            result = vault_update_frontmatter_batch(
                field, value, str(args.get("prefix", "")),
                str(args.get("match_field", "")), str(args.get("match_value", "")),
            )
            audit(persona_name, conversation_id, "vault_update_frontmatter_batch", detail)
            return result
        if name == "kubectl_read":
            detail = f"{args.get('verb', '?')} {args.get('resource', '?')}"
            audit(persona_name, conversation_id, "kubectl_read", detail)
            return kubectl_read(args)
        if name == "github_read":
            detail = f"{args.get('command', '?')} {args.get('subcommand', '?')}"
            audit(persona_name, conversation_id, "github_read", detail)
            return github_read(args)
        if name == "terminal_exec":
            command = str(args.get("command", ""))
            audit(persona_name, conversation_id, "terminal_exec", command[:500])
            return terminal_exec(args)
        if name == "nova_capture":
            target = str(args.get("target", ""))
            text = str(args.get("text", ""))
            ok, message = capture_to_backlog(target, text)
            # Audited with the text on the `after` side, the same shape the
            # site's own capture box uses (nova_site.py), so a line filed by
            # a reply and a line typed into the box read identically in the
            # Activity feed. `capture` refuses any target outside
            # CAPTURE_TARGETS, so nothing here needs to validate the path.
            audit(
                persona_name, conversation_id, "nova_capture",
                f"Capture to {target} · {'ok' if ok else message}",
                after=text, is_error=not ok,
            )
            # `FAILED` is what makes the failure visible to the *model*, not
            # just to the audit trail. `execute_tool` returns one string and
            # has no channel for "this did not work", so a 409 or a missing
            # target file came back indistinguishable from success -- and
            # the reply turn's system prompt tells it to file the thing and
            # then tell Edvard it filed it. He would have been told a bug
            # was captured when nothing was written.
            #
            # The prefix rather than a new return shape because the vault
            # writers already answer this way (`vault_write_path` returns
            # "FAILED(...)", and `_audit_vault_write` above keys on it), so
            # this is the existing convention reused rather than a second
            # one invented next to it. tools_mcp maps it to MCP's isError.
            return message if ok else f"FAILED: {message}"
        if name == "save_memory":
            memory = str(args.get("memory", ""))
            persona_id = persona.get("id")
            if not persona_id:
                return "no persistent persona record — memory not saved"
            status, _ = agora_internal("PATCH", f"/personas/{persona_id}", {"sharedMemory": memory})
            audit(persona_name, conversation_id, "save_memory", f"{len(memory)} chars")
            return "memory saved" if status == 200 else f"save failed ({status})"
        if name == "list_personas":
            status, body = agora_get("/personas")
            audit(persona_name, conversation_id, "list_personas", "")
            if status != 200:
                return f"[list_personas failed: HTTP {status}]"
            rows = [
                f"{p['id']} | {p['name']} | {p['model']}"
                for p in body.get("personas", [])
            ]
            return "\n".join(rows) or "[no personas exist yet]"
        if name == "list_models":
            status, body = agora_get("/models")
            audit(persona_name, conversation_id, "list_models", "")
            if status != 200:
                return f"[list_models failed: HTTP {status}]"
            rows = [f"{m['id']} | {m['label']}" for m in body.get("models", [])]
            return "\n".join(rows) or "[no models configured]"
        if name == "create_persona":
            payload = {
                "name": str(args.get("name", "")),
                "personality": str(args.get("personality", "")),
                "model": str(args.get("model", "")),
                "thinking": bool(args.get("thinking", False)),
            }
            status, body = agora_internal("POST", "/personas", payload)
            audit(persona_name, conversation_id, "create_persona", payload["name"])
            if status not in (200, 201):
                return f"[create_persona failed: HTTP {status} {json.dumps(body)[:300]}]"
            return f"created persona {body.get('persona', {}).get('id')} ({payload['name']})"
        if name == "create_conversation":
            payload = {"name": str(args.get("name", ""))}
            if args.get("personaId"):
                payload["personaId"] = str(args["personaId"])
            else:
                payload["personality"] = str(args.get("personality", ""))
                payload["model"] = str(args.get("model") or persona.get("model") or "")
            status, body = agora_internal("POST", "/conversations", payload)
            audit(persona_name, conversation_id, "create_conversation", payload["name"])
            if status not in (200, 201):
                return f"[create_conversation failed: HTTP {status} {json.dumps(body)[:300]}]"
            return f"created conversation {body.get('conversation', {}).get('id')} ({payload['name']})"
        if name == "create_heartbeat":
            payload = {
                "name": str(args.get("name", "")),
                "personaId": str(args.get("personaId", "")),
                "schedule": str(args.get("schedule", "")),
                "task": str(args.get("task", "")),
            }
            if args.get("conversationId"):
                payload["conversationId"] = str(args["conversationId"])
            elif args.get("newConversationName"):
                payload["newConversationName"] = str(args["newConversationName"])
            else:
                return "[create_heartbeat error: conversationId or newConversationName is required]"
            # Issues.md: "creating heartbeats using agent tool seems to
            # create two heartbeats instead of one" -- root cause is
            # the retry path FAILURE_BACKOFF_CAP guards (see config.py):
            # a turn that calls this tool successfully in one round and
            # then errors in a LATER round never appends a reply, so the
            # whole turn (including this tool call) is resent verbatim on
            # the next poll tick. Same defensive check create_pr already
            # does for its branch/PR ("reuse rather than duplicate") --
            # same name for the same persona is treated as a retry, not
            # a genuinely new heartbeat.
            existing_status, existing_body = agora_internal("GET", "/heartbeats")
            if existing_status == 200:
                for hb in existing_body.get("heartbeats", []):
                    if hb.get("name") == payload["name"] and hb.get("personaId") == payload["personaId"]:
                        audit(persona_name, conversation_id, "create_heartbeat",
                              f"{payload['name']} (reused existing {hb.get('id')}, not duplicated)")
                        return f"heartbeat {hb.get('id')} ({payload['name']}) already exists -- reused, not duplicated"
            status, body = agora_internal("POST", "/heartbeats", payload)
            audit(persona_name, conversation_id, "create_heartbeat", payload["name"])
            if status not in (200, 201):
                return f"[create_heartbeat failed: HTTP {status} {json.dumps(body)[:300]}]"
            return f"created heartbeat {body.get('heartbeat', {}).get('id')} ({payload['name']})"
        if name == "create_workflow":
            payload = {
                "name": str(args.get("name", "")),
                "description": str(args.get("description", "")),
                "steps": args.get("steps") or [],
            }
            status, body = agora_internal("POST", "/workflows", payload)
            audit(persona_name, conversation_id, "create_workflow", payload["name"])
            if status not in (200, 201):
                return f"[create_workflow failed: HTTP {status} {json.dumps(body)[:300]}]"
            return f"created workflow {body.get('workflow', {}).get('id')} ({payload['name']})"
        if name == "create_pr":
            repo = str(args.get("repo", ""))
            branch = str(args.get("branch", ""))
            files = args.get("files") or []
            result = create_pr(
                repo, branch, files,
                str(args.get("commit_message", "")), str(args.get("title", "")),
                str(args.get("body", "")), str(args.get("base") or "main"),
            )
            audit(persona_name, conversation_id, "create_pr", f"{repo}#{branch} ({len(files)} file(s))")
            return result
        if name == "github_comment":
            repo = str(args.get("repo", ""))
            issue_number = int(args.get("issue_number") or 0)
            result = github_comment(repo, issue_number, str(args.get("body", "")))
            audit(persona_name, conversation_id, "github_comment", f"{repo}#{issue_number}")
            return result
        if name == "merge_pr":
            repo = str(args.get("repo", ""))
            pr_number = int(args.get("pr_number") or 0)
            result = merge_pr(repo, pr_number, str(args.get("merge_method") or "squash"))
            audit(persona_name, conversation_id, "merge_pr", f"{repo}#{pr_number}")
            return result
        if name == "scoped_write":
            if active_step is None or not active_step.get("filepath"):
                return "[scoped_write error: no active step file scope]"
            target = _resolve_scoped_target(active_step, args)
            if target is None:
                return "[scoped_write error: folder target requires a valid filename on the first call]"
            content = str(args.get("content", ""))
            before = _before_snapshot(target)
            result = _conditional_write(conversation_id, target, content)
            _audit_vault_write(persona_name, conversation_id, "scoped_write", target, result, before, content)
            return result
        return f"[unknown tool {name}]"
    except Exception as e:
        return f"[tool error: {e}]"
