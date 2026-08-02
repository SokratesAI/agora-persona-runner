"""execute_tool -- the single dispatch point every provider calls for every tool_use block."""

import json

from agora_runner.config import FAILURE_PAUSE_CAP
from agora_runner.log import debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.audit import audit
from agora_runner.vault import (
    vault_read_path, vault_write_path, vault_append_path, vault_list_prefix, vault_search,
    vault_query_frontmatter, vault_validate_frontmatter_schema, vault_find_stub_notes,
    vault_find_duplicate_titles, vault_get_token_metrics, vault_git_revision_history,
    vault_summarize_recent_agent_work, vault_update_frontmatter_batch,
)
from agora_runner.tools_kubectl import kubectl_read
from agora_runner.tools_github import github_read, create_pr, github_comment, merge_pr
from agora_runner.tools_terminal import terminal_exec
from agora_runner.tools_search import web_search_tinyfish


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
            content = vault_read_path(path)
            return content if content is not None else f"[not found: {path}]"
        if name == "vault_list":
            prefix = str(args.get("prefix", ""))
            audit(persona_name, conversation_id, "vault_list", prefix)
            paths = vault_list_prefix(prefix)
            return "\n".join(paths[:200]) or "[no files under that prefix]"
        if name == "vault_write":
            path = str(args.get("path", ""))
            content = str(args.get("content", ""))
            # Read before overwriting so the audit entry can carry a
            # real before/after pair for the Activity diff view. Best
            # effort -- a failed read (e.g. new file) just means "" as
            # the before side, same as the file not existing.
            before = vault_read_path(path) or ""
            result = vault_write_path(path, content)
            audit(persona_name, conversation_id, "vault_write", path, before=before, after=content)
            return result
        if name == "vault_append":
            path = str(args.get("path", ""))
            content = str(args.get("content", ""))
            after_marker = str(args.get("after_marker", ""))
            before = vault_read_path(path) or ""
            result = vault_append_path(path, content, after_marker)
            audit(persona_name, conversation_id, "vault_append", path, before=before, after=content)
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
            # FAILURE_PAUSE_CAP's own retry path (see its comment above):
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
            before = vault_read_path(target) or ""
            result = vault_write_path(target, content)
            audit(persona_name, conversation_id, "scoped_write", target, before=before, after=content)
            return result
        return f"[unknown tool {name}]"
    except Exception as e:
        return f"[tool error: {e}]"
