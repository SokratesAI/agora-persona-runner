"""Pure turn-taking/scheduling logic and system-prompt construction -- no I/O of its own."""

import re
from datetime import datetime, timedelta, timezone

from agora_runner.config import AI_TURN_CAP, MAX_HISTORY, OSLO


def parse_mentions(text, names):
    """Ordered, de-duplicated persona names @mentioned in text.
    Case-insensitive; longest names matched first so '@Marcus-2' can't be
    swallowed by a persona named 'Marcus'."""
    found = []
    lowered = text.lower()
    for name in sorted(names, key=len, reverse=True):
        pattern = re.compile(r"@" + re.escape(name.lower()) + r"(?![\w-])")
        for match in pattern.finditer(lowered):
            found.append((match.start(), name))
    found.sort()
    ordered = []
    for _pos, name in found:
        if name not in ordered:
            ordered.append(name)
    return ordered


def consecutive_ai_turns(thread):
    """Counts TURNS, not messages -- a run of consecutive same-sender
    messages is one turn. 2026-07-24: a single logical turn now lands as
    several messages (one per streamed text block), so counting raw
    messages would trip AI_TURN_CAP many times faster than intended and
    auto-pause a chain that's really only had 1-2 real handoffs."""
    count = 0
    last_sender = None
    for message in reversed(thread):
        sender = message.get("sender")
        if sender == "Edvard":
            break
        if sender != last_sender:
            count += 1
            last_sender = sender
    return count


PAUSE_SENTINEL = "__PAUSE__"


def decide_turn(thread, personas):
    """Architecture §3. Returns ordered speaker names, [] for nothing, or
    [PAUSE_SENTINEL] when a chain hit the cap and must auto-pause.

    `activity` messages (2026-07-24 inline Activity chips) are excluded
    from `visible` same as `forgotten` -- they're not conversation
    content, just a UI event marker for a tool call. This also makes
    retry-after-failure work: if a turn posts a text chunk then fails on
    a later round, speak()/run_heartbeat() roll that chunk back out, but
    any activity chip from a tool call that genuinely already ran is
    deliberately left in place (real audit trail); without this
    exclusion a leftover chip as the thread's last message would look
    like "a persona already replied" and the failed turn would never
    retry.

    `thinking` messages (2026-07-31) excluded for the same reason as
    `activity` -- a persona's own extended-thinking chunk is not
    something it "said" to anyone, and a leftover one as the thread's
    last message would look like an already-completed reply the same
    way a stray activity chip would."""
    visible = [m for m in thread if not m.get("forgotten") and not m.get("activity") and not m.get("thinking")]
    if not visible:
        return []
    names = [p["name"] for p in personas]
    curator = next((p["name"] for p in personas if p.get("role") == "curator"), None)
    last = visible[-1]

    if last.get("sender") == "Edvard":
        mentioned = parse_mentions(last.get("text", ""), names)
        if mentioned:
            return mentioned
        return [curator] if curator else []

    # Last message from a persona — chains continue only via @mention.
    mentioned = [
        n for n in parse_mentions(last.get("text", ""), names) if n != last.get("sender")
    ]
    if not mentioned:
        return []
    if consecutive_ai_turns(visible) >= AI_TURN_CAP:
        return [PAUSE_SENTINEL]
    return mentioned[:1]


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def schedule_due(schedule, last_run_iso, created_iso, now_utc):
    """Idempotent due computation — no in-memory scheduler state, so a
    runner restart can never double-fire (critique on Decisions/0006).
    createdAt floors the first run: a daily@08:00 heartbeat created at
    14:00 waits for tomorrow instead of surprise-firing at creation."""
    floor = parse_iso(last_run_iso or created_iso)
    if schedule.startswith("every@"):
        amount = schedule[len("every@"):]
        value, unit = int(amount[:-1]), amount[-1]
        delta = timedelta(minutes=value) if unit == "m" else timedelta(hours=value)
        return now_utc >= floor + delta
    if schedule.startswith("daily@"):
        hh, mm = schedule[len("daily@"):].split(":")
        now_oslo = now_utc.astimezone(OSLO)
        occurrence = now_oslo.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if occurrence > now_oslo:
            occurrence -= timedelta(days=1)
        return occurrence.astimezone(timezone.utc) > floor
    return False


def merge_history(thread, self_name, multi):
    """Provider-ready history: Edvard → user; personas → assistant, other
    personas' lines prefixed "[Name]:" in multi-persona threads so models
    can tell speakers apart; consecutive same-role turns merged; leading
    assistant turns dropped (both providers want a user turn first);
    forgotten, system, AND activity messages excluded everywhere. Each
    entry also carries `attachments` (may be empty) -- 2026-07-24, see
    fetch_attachment_bytes -- so anthropic_generate/gemini_generate can
    build real image content blocks instead of silently losing everything
    but the caption text.

    `system` exclusion is also 2026-07-24: found live that a persona
    asked an unrelated question shortly after an auto-pause notice
    answered about THAT notice instead, having read it as a real previous
    reply -- sender-name matching ("Agora") can't distinguish a
    control-plane notice from a real persona literally named Agora (the
    legacy Main-thread migration creates exactly one), so this needs the
    conversation-store's own `system` flag, not a name check.

    `activity` exclusion, same day: inline Activity chips (a tool call
    completing) are a UI event, not something anyone said -- a persona
    must never see its own or another's tool use reported back as if it
    were real conversation content.

    `thinking` exclusion (2026-07-31): a persona's own extended-thinking
    chunk is scratch space, not something anyone said -- feeding it back
    as if it were a real previous turn would be redundant at best and
    confusing at worst (a model re-reading its own raw train of thought
    as context, or another persona in a multi-persona thread reading it
    as if it were an actual statement)."""
    merged = []
    for message in thread[-MAX_HISTORY:]:
        if message.get("forgotten") or message.get("system") or message.get("activity") or message.get("thinking"):
            continue
        sender = message.get("sender", "")
        text = message.get("text", "")
        attachments = message.get("attachments") or []
        if sender == "Edvard":
            role, content = "user", text
        else:
            role = "assistant"
            content = f"[{sender}]: {text}" if (multi and sender != self_name) else text
        if merged and merged[-1]["role"] == role:
            if content:
                merged[-1]["content"] += "\n\n" + content
            merged[-1]["attachments"].extend(attachments)
        else:
            merged.append({"role": role, "content": content, "attachments": list(attachments)})
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


def build_system(persona, conversation=None, participants=None, heartbeat_extra=None):
    parts = [persona.get("personality") or "You are a helpful assistant."]
    shared = (persona.get("sharedMemory") or "").strip()
    if shared:
        parts.append(f"## Your persistent memory\n{shared}")
    if conversation:
        memory = (conversation.get("memory") or "").strip()
        if memory:
            parts.append(f"## Conversation notes\n{memory}")
    if participants and len(participants) > 1:
        roster = ", ".join(
            f"{p['name']} (curator)" if p.get("role") == "curator" else p["name"]
            for p in participants
        )
        parts.append(
            f"## Participants\nEdvard plus personas: {roster}. You are {persona.get('name')}. "
            "Address another persona by writing @TheirName — only personas that are "
            "@mentioned may reply. Do not @mention anyone unless you genuinely want "
            "them to answer next."
        )
    caps = persona.get("capabilities") or {}
    if caps.get("vaultRead") or caps.get("vaultWrite"):
        parts.append(
            "## Vault access\nYou have tools for Edvard's Obsidian vault. Paths are "
            "case-insensitive; folders end with '/'. save_memory REPLACES your entire "
            "persistent memory — always include everything you still want to keep. "
            "Beyond single-file read/write you also have vault_search (full-text), "
            "vault_query_frontmatter, vault_validate_frontmatter_schema, "
            "vault_find_stub_notes, vault_find_duplicate_titles, vault_get_token_metrics, "
            "vault_git_revision_history and vault_summarize_recent_agent_work (both "
            "against the daily backup mirror on GitHub)"
            + (", and vault_update_frontmatter_batch for bulk metadata edits" if caps.get("vaultWrite") else "")
            + "."
        )
    if caps.get("kubectlRead"):
        parts.append(
            "## Cluster access (read-only)\nYou have kubectl_read for cluster "
            "introspection — get/describe/logs/top, cluster-wide, on non-Secret "
            "resources only. Reading Secret objects is refused at both the tool "
            "and RBAC level; don't try. You cannot create, modify, delete, or "
            "exec into anything."
        )
    if caps.get("githubRead"):
        parts.append(
            "## GitHub access (read-only)\nYou have github_read for read-only "
            "GitHub queries (issues, PRs, runs, releases, repo info) via `gh`. "
            "You cannot open PRs/issues, push, comment, or change anything."
        )
    if caps.get("manageAgora"):
        own_id = persona.get("id")
        parts.append(
            "## Manage Agora\nYou have create_persona, create_conversation, "
            "create_heartbeat, and create_workflow — these create real platform "
            "objects immediately, not drafts. Use them deliberately, not speculatively. "
            + (f"Your own personaId is {own_id} — use this directly for create_heartbeat "
               "or create_conversation when the action is about yourself, no lookup "
               "needed. " if own_id else "")
            + "create_persona and create_conversation require an exact "
            "'<provider>:<model id>' string (e.g. 'anthropic:claude-sonnet-5') — call "
            "list_models first rather than guessing the format. To create a heartbeat "
            "for an existing persona other than yourself, call list_personas first to "
            "get its id — there is no other way to look one up."
        )
    if caps.get("githubWrite") or caps.get("githubMerge"):
        gh_parts = ["## GitHub write access"]
        if caps.get("githubWrite"):
            gh_parts.append(
                "You have create_pr — opens a real PR (or adds commits to one you "
                "already opened) on any repo the bot account can reach. Pick a branch "
                "name that reflects the actual change, not a generic one."
            )
        if caps.get("githubMerge"):
            gh_parts.append(
                "You have merge_pr — merges an open PR, but only once every check-run "
                "on it is green. It refuses otherwise; there is no override."
            )
        parts.append(" ".join(gh_parts))
    if heartbeat_extra:
        parts.append(heartbeat_extra)
    return "\n\n".join(parts)
