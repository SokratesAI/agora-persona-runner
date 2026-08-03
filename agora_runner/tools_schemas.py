"""Capability-gated tool schemas for both providers, and the persona-capabilities <-> tool-name map."""

from agora_runner.config import NO_CAPS
from agora_runner.tools_kubectl import KUBECTL_ALLOWED_VERBS, KUBECTL_ALLOWED_FLAGS
from agora_runner.tools_github import GITHUB_ALLOWED_SUBCOMMANDS
from agora_runner.tools_terminal import TERMINAL_EXEC_OUTPUT_MAX


def client_tool_schemas(caps, active_step=None):
    tools = []
    if caps.get("webSearch"):
        tools.append({
            "name": "web_search",
            "description": "Search the web (DuckDuckGo) and get back titles, URLs, and snippets for the top results.",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                             "required": ["query"]},
        })
    if caps.get("vaultRead"):
        tools.append({
            "name": "vault_read",
            "description": (
                "Read one file from Edvard's Obsidian vault by path. "
                "Paths are always lowercase -- e.g. 'projects/sokrates/projects/agora/issues.md', "
                "never 'Projects/Sokrates/...'."
            ),
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                             "required": ["path"]},
        })
        tools.append({
            "name": "vault_list",
            "description": (
                "List vault file paths under a prefix (use '' for a shallow overview via known "
                "folders). Paths are always lowercase."
            ),
            "input_schema": {"type": "object", "properties": {"prefix": {"type": "string"}},
                             "required": ["prefix"]},
        })
        tools.append({
            "name": "vault_search",
            "description": (
                "Full-text search across every vault file's body (regex or plain substring, "
                "case-insensitive). Returns matching 'path:line: snippet' lines, capped at max_results."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "prefix": {"type": "string", "description": "optional folder to scope the search to"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        })
        tools.append({
            "name": "vault_query_frontmatter",
            "description": (
                "Find vault files by YAML frontmatter field, e.g. field='status', value='active'. "
                "Omit value to just require the field to be present."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "value": {"type": "string"},
                    "prefix": {"type": "string"},
                },
                "required": ["field"],
            },
        })
        tools.append({
            "name": "vault_validate_frontmatter_schema",
            "description": (
                "Scan vault files under prefix for missing required frontmatter (the vault "
                "convention: every agent-owned/edited file needs a 'type' key). Root capture "
                "files (Inbox.md etc.) are exempted."
            ),
            "input_schema": {"type": "object", "properties": {"prefix": {"type": "string"}}, "required": []},
        })
        tools.append({
            "name": "vault_find_stub_notes",
            "description": (
                "Find vault files whose body (excluding frontmatter) is suspiciously short -- "
                "likely-unfinished notes."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"prefix": {"type": "string"}, "min_chars": {"type": "integer"}},
                "required": [],
            },
        })
        tools.append({
            "name": "vault_find_duplicate_titles",
            "description": "Find vault files whose title (first H1, or filename) collides with another file's.",
            "input_schema": {"type": "object", "properties": {"prefix": {"type": "string"}}, "required": []},
        })
        tools.append({
            "name": "vault_get_token_metrics",
            "description": (
                "Approximate token/word counts per vault file under prefix (chars/4 heuristic, not "
                "a real tokenizer), sorted largest-first -- useful for spotting files at risk of "
                "blowing a context window."
            ),
            "input_schema": {"type": "object", "properties": {"prefix": {"type": "string"}}, "required": []},
        })
        tools.append({
            "name": "vault_git_revision_history",
            "description": (
                "Git history of the vault's daily backup mirror (SokratesAI/vault on GitHub). "
                "Give path to scope to one file's commits, or sha to see that commit's own file "
                "diffs instead of a log."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                    "sha": {"type": "string"},
                },
                "required": [],
            },
        })
        tools.append({
            "name": "vault_summarize_recent_agent_work",
            "description": (
                "Changelog of vault activity (commits + files touched) over the last N hours, "
                "from the daily backup mirror."
            ),
            "input_schema": {"type": "object", "properties": {"hours": {"type": "integer"}}, "required": []},
        })
    if caps.get("vaultWrite"):
        tools.append({
            "name": "vault_write",
            "description": (
                "Create or overwrite one vault file. The previous version is automatically backed "
                "up to agora/backups/ first. Paths are always lowercase -- use exactly the casing "
                "an existing file already has (check with vault_list/vault_read first), and use "
                "all-lowercase for a brand new file. Never write a different-cased variant of an "
                "existing path, e.g. 'Issues.md' vs 'issues.md' -- the backend normalizes to "
                "lowercase either way, but mismatched casing across calls has previously produced "
                "confusing duplicate-looking folders on the client."
            ),
            "input_schema": {"type": "object",
                             "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                             "required": ["path", "content"]},
        })
        tools.append({
            "name": "vault_append",
            "description": (
                "Add content to an EXISTING vault file WITHOUT losing what's already there -- "
                "use this instead of vault_write for any append-only log (e.g. an evolution/"
                "cycle journal): vault_write replaces the WHOLE file, so calling it with only "
                "your new entry silently destroys every prior one. Give after_marker as a line "
                "that already exists in the file (e.g. '## Entries') to insert content right "
                "after it; omit after_marker to just add content at the end of the file. Fails "
                "if the file doesn't exist yet -- use vault_write to create a new file -- and "
                "fails without writing anything if after_marker matches no line, so check the "
                "result and retry with a marker you have actually read in the file."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "after_marker": {"type": "string", "description": "optional: an existing line to insert content directly after; must match a real line or the call fails without writing"},
                },
                "required": ["path", "content"],
            },
        })
        tools.append({
            "name": "vault_update_frontmatter_batch",
            "description": (
                "Bulk-set one frontmatter field to one value across every vault file under prefix "
                "(optionally only files where match_field currently contains match_value). Rewrites "
                "only that key's line, preserving everything else in each file untouched. Same "
                "automatic per-file backup as vault_write."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "value": {"type": "string"},
                    "prefix": {"type": "string"},
                    "match_field": {"type": "string"},
                    "match_value": {"type": "string"},
                },
                "required": ["field", "value"],
            },
        })
    if caps.get("kubectlRead"):
        tools.append({
            "name": "kubectl_read",
            "description": (
                "Read-only Kubernetes cluster introspection: get/describe/logs/top on "
                "non-Secret resources, cluster-wide. Never returns Secret contents -- "
                "blocked at both the tool and RBAC level, not just this description."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "verb": {"type": "string", "enum": sorted(KUBECTL_ALLOWED_VERBS)},
                    "resource": {"type": "string",
                                 "description": "e.g. 'pods', 'deployment/agora', 'pod/agora-xyz-123'"},
                    "namespace": {"type": "string", "description": "omit for --all-namespaces"},
                    "args": {"type": "array", "items": {"type": "string"},
                             "description": f"extra flags only from {sorted(KUBECTL_ALLOWED_FLAGS)}"},
                },
                "required": ["verb", "resource"],
            },
        })
    if caps.get("githubRead"):
        tools.append({
            "name": "github_read",
            "description": (
                "Read-only GitHub queries via `gh`: issues, PRs, runs, workflows, "
                "releases, repo info. Cannot open/comment/push/merge anything. "
                "There's no dedicated command for listing commits -- use "
                "command='api', subcommand='/repos/{owner}/{repo}/commits', "
                "args=['-f', 'per_page=1'] (do NOT also put the path in args --"
                " subcommand IS the full request path for 'api')."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "enum": sorted(GITHUB_ALLOWED_SUBCOMMANDS)},
                    "subcommand": {
                        "type": "string",
                        "description": (
                            "e.g. 'list', 'view' -- allowed values depend on command. "
                            "For command='api', this is instead the full request path, "
                            "e.g. '/repos/SokratesAI/agora/commits'."
                        ),
                    },
                    "args": {"type": "array", "items": {"type": "string"},
                             "description": "extra positional/flag args, e.g. '-f', 'per_page=1', '--limit=10'"},
                },
                "required": ["command", "subcommand"],
            },
        })
    if caps.get("terminalExec"):
        tools.append({
            "name": "terminal_exec",
            "description": (
                "Run an arbitrary shell command in this runner pod (bash -lc). "
                "Unrestricted -- no command allowlist, unlike kubectl_read/github_read. "
                "Use it to inspect or fix anything no purpose-built tool covers yet, run "
                "git/npm/python/etc. directly, or debug a failing build. Runs in a "
                "persistent per-pod scratch workspace that carries over between calls in "
                "the same task (not across a pod restart). Output is truncated at "
                f"{TERMINAL_EXEC_OUTPUT_MAX} chars; the command is killed after `timeout` "
                "seconds (default 60, max 300)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "shell command, e.g. 'git clone ... && npm test'"},
                    "timeout": {"type": "integer", "description": "seconds, default 60, max 300"},
                    "cwd": {"type": "string", "description": "relative subdir inside the scratch workspace, created if missing"},
                },
                "required": ["command"],
            },
        })
    if caps.get("githubWrite"):
        tools.append({
            "name": "create_pr",
            "description": (
                "Open a GitHub PR (or push more commits onto one you already opened) "
                "using the shared bot account -- no git needed, just give the branch "
                "name and whole-file contents to write. Any repo the bot account can "
                "reach, e.g. 'agora', 'platform-config', 'platform-workers'. Pick a "
                "branch name that reflects the change (e.g. 'fix-heartbeat-typo'), not "
                "a generic one -- it's never auto-generated."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "repo name within the SokratesAI org, e.g. 'agora'"},
                    "branch": {"type": "string", "description": "new (or existing) branch name -- never 'main'/'master'"},
                    "base": {"type": "string", "description": "branch to base off of, default 'main'"},
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                            "required": ["path", "content"],
                        },
                        "description": "whole-file contents to create or overwrite on the branch",
                    },
                    "commit_message": {"type": "string"},
                    "title": {"type": "string", "description": "PR title -- used the first time a branch's PR is opened"},
                    "body": {"type": "string"},
                },
                "required": ["repo", "branch", "files", "commit_message", "title"],
            },
        })
        tools.append({
            "name": "github_comment",
            "description": (
                "Post a comment on a GitHub issue or pull request using the shared bot "
                "account. Works for both -- pass the issue number or the PR number, "
                "they share one numbering space. This comments on the conversation "
                "thread, not on a specific line of a diff."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "repo name within the SokratesAI org, e.g. 'agora'"},
                    "issue_number": {"type": "integer", "description": "issue or PR number, e.g. 42"},
                    "body": {"type": "string", "description": "comment body (markdown)"},
                },
                "required": ["repo", "issue_number", "body"],
            },
        })
    if caps.get("githubMerge"):
        tools.append({
            "name": "merge_pr",
            "description": (
                "Merge an open PR -- refuses unless every check-run on its head commit "
                "has completed with a passing conclusion. Does not check who opened the "
                "PR (every agent shares the same GitHub account, so that check would be "
                "meaningless)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "pr_number": {"type": "integer"},
                    "merge_method": {"type": "string", "enum": ["squash", "merge", "rebase"]},
                },
                "required": ["repo", "pr_number"],
            },
        })
    if caps.get("manageAgora"):
        tools.append({
            "name": "list_personas",
            "description": (
                "List every existing persona (id, name, model, capabilities). Call this "
                "before create_heartbeat/create_conversation when you need an EXISTING "
                "persona other than yourself -- there is no other way to look one up. "
                "(Your own personaId is already given to you above, no lookup needed.)"
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        })
        tools.append({
            "name": "list_models",
            "description": (
                "List every valid model id string (e.g. 'anthropic:claude-sonnet-5', "
                "'gemini:gemini-flash-latest'). Call this before create_persona or "
                "create_conversation rather than guessing the format."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        })
        tools.append({
            "name": "create_persona",
            "description": "Create a new Agora persona (name, personality/system prompt, model, thinking).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "personality": {"type": "string"},
                    "model": {
                        "type": "string",
                        "description": "e.g. 'anthropic:claude-sonnet-5' or 'gemini:gemini-flash-latest'",
                    },
                    "thinking": {"type": "boolean"},
                },
                "required": ["name", "model"],
            },
        })
        tools.append({
            "name": "create_conversation",
            "description": (
                "Create a new empty conversation (channel) -- either with an existing persona "
                "(personaId) or a brand new inline one (personality/model, defaults to your own "
                "model if omitted)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "personaId": {"type": "string"},
                    "personality": {"type": "string"},
                    "model": {"type": "string"},
                },
                "required": ["name"],
            },
        })
        tools.append({
            "name": "create_heartbeat",
            "description": (
                "Create a new scheduled heartbeat. schedule is 'daily@HH:MM' (Europe/Oslo) or "
                "'every@N[m|h]'. Give conversationId for an existing channel, or "
                "newConversationName to create a fresh empty one just for this heartbeat."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "personaId": {"type": "string"},
                    "conversationId": {"type": "string"},
                    "newConversationName": {"type": "string"},
                    "schedule": {"type": "string"},
                    "task": {"type": "string"},
                },
                "required": ["name", "personaId", "schedule"],
            },
        })
        tools.append({
            "name": "create_workflow",
            "description": (
                "Create a new workflow (a named sequence of steps for Heartbeat-triggered "
                "multi-persona execution, Decisions/0009). steps is a list of "
                "{prompt, loopCount} objects -- pass an empty list for a workflow you'll fill "
                "in later from the Studio."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string"},
                                "loopCount": {"type": "integer"},
                            },
                        },
                    },
                },
                "required": ["name"],
            },
        })
    # save_memory always available to real personas — closes the memory loop.
    tools.append({
        "name": "save_memory",
        "description": "Replace your entire persistent cross-conversation memory with new content. Include everything you still want to keep.",
        "input_schema": {"type": "object", "properties": {"memory": {"type": "string"}},
                         "required": ["memory"]},
    })
    # Decisions/0009 — scoped_write is a workflow-step tool, never gated
    # by persona.capabilities.vaultWrite: it can only ever target the
    # one file (or, for a folder scope, the one file named on the
    # FIRST call this step) configured on the active step, enforced
    # inside execute_tool itself, not just by this schema's presence.
    if active_step and "scoped_write" in (active_step.get("toolWhitelist") or []) and active_step.get("filepath"):
        is_folder = active_step["filepath"].endswith("/")
        properties = {"content": {"type": "string"}}
        if is_folder:
            properties["filename"] = {
                "type": "string",
                "description": "Only used on the first scoped_write call this step — picks the file inside the folder, then locked for the rest of the step.",
            }
        tools.append({
            "name": "scoped_write",
            "description": (
                "Write to this step's assigned file scope. This tool can only ever "
                "target the file configured for this workflow step — you cannot "
                "direct it anywhere else, regardless of your own vaultWrite capability. "
                + (f"This step's scope is a folder ({active_step['filepath']}); give just "
                   "a filename, e.g. 'notes.md'. The first call's filename picks the file, "
                   "every later call this step writes to that same file."
                   if is_folder else
                   f"This step's target file is fixed: {active_step['filepath']}")
            ),
            "input_schema": {"type": "object", "properties": properties, "required": ["content"]},
        })
    return tools




TOOL_TO_CAPABILITY = {
    "web_search": "webSearch",
    "vault_read": "vaultRead",
    "vault_list": "vaultRead",
    "vault_search": "vaultRead",
    "vault_query_frontmatter": "vaultRead",
    "vault_validate_frontmatter_schema": "vaultRead",
    "vault_find_stub_notes": "vaultRead",
    "vault_find_duplicate_titles": "vaultRead",
    "vault_get_token_metrics": "vaultRead",
    "vault_git_revision_history": "vaultRead",
    "vault_summarize_recent_agent_work": "vaultRead",
    "vault_write": "vaultWrite",
    "vault_append": "vaultWrite",
    "vault_update_frontmatter_batch": "vaultWrite",
    "kubectl_read": "kubectlRead",
    "github_read": "githubRead",
    "terminal_exec": "terminalExec",
    "list_personas": "manageAgora",
    "list_models": "manageAgora",
    "create_persona": "manageAgora",
    "create_conversation": "manageAgora",
    "create_heartbeat": "manageAgora",
    "create_workflow": "manageAgora",
    "create_pr": "githubWrite",
    "github_comment": "githubWrite",
    "merge_pr": "githubMerge",
}


def capabilities_for_step(persona, step):
    """Decisions/0009 — a step's toolWhitelist narrows what's advertised
    for that step only, never widens beyond the persona's own grant.
    Empty/unset whitelist = unrestricted (persona's own caps apply
    as-is). scoped_write isn't in this map at all — it's gated purely
    by active_step in client_tool_schemas, never by any capability.

    vault_read and vault_list both map to vaultRead — grouped by
    cap_key (not checked tool-by-tool) so whitelisting just one of the
    two doesn't clobber the capability the other already earned. The
    Studio's "Vault read" checkbox always adds both together, but this
    function has to be correct for a direct API call that doesn't."""
    caps = dict(persona.get("capabilities") or NO_CAPS)
    whitelist = step.get("toolWhitelist") or []
    if whitelist:
        cap_keys_present = {
            cap_key for tool, cap_key in TOOL_TO_CAPABILITY.items() if tool in whitelist
        }
        for cap_key in set(TOOL_TO_CAPABILITY.values()):
            if cap_key not in cap_keys_present:
                caps[cap_key] = False
    return caps
