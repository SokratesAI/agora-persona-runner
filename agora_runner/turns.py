"""Pure turn-taking/scheduling logic and system-prompt construction -- no I/O of its own."""

from datetime import datetime, timedelta, timezone

from agora_runner.config import MAX_HISTORY, OSLO


def decide_turn(thread, personas):
    """Who speaks next. Returns [name] or [] for nothing.

    A conversation holds exactly one persona as of agora#67 (Agora
    refuses a second one), so the only question left is whether it is
    that persona's turn, and it is whenever the owner spoke last. The
    @mention speaker selection this used to run -- `parse_mentions`, the
    persona-to-persona chain rule, `consecutive_ai_turns`/`AI_TURN_CAP`
    and `PAUSE_SENTINEL` -- was all reachable only with two or more
    personas in one thread and went with them. Measured 2026-08-25 before
    deleting it: 405 conversations, every one holding exactly one
    persona, every one of those a curator.

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
    if visible[-1].get("sender") != "Edvard":
        return []
    if not personas:
        return []
    # The curator preference is kept because that is what all 405 live
    # conversations carry. The fallback to the first participant is not
    # cosmetic: the old code answered a lone non-curator persona only when
    # the owner @mentioned it by name, so dropping the @mention path without
    # this would have made such a conversation silently never reply.
    curator = next((p["name"] for p in personas if p.get("role") == "curator"), None)
    return [curator or personas[0]["name"]]


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def last_anchored_occurrence(anchor, delta, now_utc):
    """The most recent slot at or before now, for an interval pinned to an
    Oslo wall-clock time — `every@6h@12:00` means 12:00, 18:00, 00:00,
    06:00, the same times every day, instead of `interval` after whenever
    the last run happened to be.

    Each Oslo day lays its own grid out from the anchor, which only holds
    together when the interval divides 24h — and Agora rejects an anchored
    schedule where it doesn't (isValidSchedule, heartbeat-store.ts). With
    `every@7h@12:00` the slots are 05:00/12:00/19:00, but at 00:30 the walk
    back from *today's* 12:00 lands on 22:00 the night before, a slot that
    did not exist when you asked at 23:30 — so it fires an extra time every
    midnight. That discontinuity is the reason for the divisibility rule;
    test_a_non_dividing_interval_is_why_agora_rejects_one pins it.

    Arithmetic is done on naive wall-clock and localised afterwards. Doing it
    on aware datetimes gives the same answers (subtracting two datetimes that
    share a tzinfo is defined as wall-clock, and zoneinfo recomputes the
    offset on the way out) — the naive round-trip is here so the reader
    doesn't have to know that rule to see the times are wall-clock, not
    elapsed. Either way they survive a DST shift; on the spring-forward day a
    slot landing in the missing hour resolves forward, which is standard
    zoneinfo behaviour and not worth a special case."""
    hh, mm = (int(part) for part in anchor.split(":"))
    naive_now = now_utc.astimezone(OSLO).replace(tzinfo=None)
    base = naive_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    occurrence = base + ((naive_now - base) // delta) * delta
    return occurrence.replace(tzinfo=OSLO).astimezone(timezone.utc)


# Day-of-week runs to 7, not 6: 0 and 7 both mean Sunday in every cron
# implementation, and 7 is folded back to 0 once the range is expanded, so
# "1-7" reads as Mon-Sun the way anyone writing it expects.
CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
# How far back last_cron_occurrence will walk before giving up. A day-of-month
# plus month restriction can legitimately be years apart -- "0 0 29 2 *" is
# Feb 29, which 2100 skips, so the true worst-case gap is 8 years. Walking that
# is a few thousand set lookups and only ever happens for an expression that
# rare; the alternative (a smaller bound) would silently report "never ran".
CRON_LOOKBACK_DAYS = 8 * 366


def parse_cron_field(field, index):
    """One cron field -> the set of values it matches. Supports `*`, `N`,
    `a-b`, and a `/step` suffix on any of those (`*/15`, `8-22/2`), plus
    comma-separated lists of all of them. Day-of-week takes 7 as Sunday as
    well as 0, which is what every cron implementation does and what anyone
    hand-writing `1-7` expects.

    Raises ValueError on anything else rather than guessing -- Agora's
    isValidSchedule rejects the same strings at the route, so a bad
    expression should not reach here at all, and if one does we would much
    rather see it than run a schedule nobody asked for."""
    low, high = CRON_RANGES[index]
    matched = set()
    for part in field.split(","):
        spec, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if step < 1:
            raise ValueError(f"cron step must be >= 1: {part!r}")
        if spec == "*":
            start, end = low, high
        elif "-" in spec:
            start_text, _, end_text = spec.partition("-")
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(spec)
            if step_text:  # "5/15" is meaningless -- a bare value has no range
                raise ValueError(f"cron step needs a range or *: {part!r}")
        if not (low <= start <= high and low <= end <= high) or start > end:
            raise ValueError(f"cron field out of range: {part!r}")
        matched.update(range(start, end + 1, step))
    if index == 4 and 7 in matched:
        matched.discard(7)
        matched.add(0)
    return matched


def parse_cron(expr):
    """`minute hour day-of-month month day-of-week` -> five value sets."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron needs exactly 5 fields, got {len(fields)}: {expr!r}")
    return [parse_cron_field(f, i) for i, f in enumerate(fields)], fields


def cron_day_matches(day, doms, months, dows, dom_restricted, dow_restricted):
    """Vixie cron's day rule, deliberately reproduced rather than simplified:
    when BOTH day-of-month and day-of-week are restricted the day matches if
    EITHER does (so `0 0 1 * 1` is the 1st *and* every Monday), but when only
    one is restricted the other is ignored. Getting this "wrong but sensible"
    -- ANDing them -- is the classic cron bug, and someone reading `0 8 1 * 1`
    in the raw-expression box will be expecting the standard meaning."""
    if day.month not in months:
        return False
    dow = (day.weekday() + 1) % 7  # Python Mon=0 -> cron Sun=0
    if dom_restricted and dow_restricted:
        return day.day in doms or dow in dows
    return day.day in doms and dow in dows


def last_cron_occurrence(expr, now_utc):
    """The most recent slot at or before now, as UTC, for a cron expression
    read in Oslo wall-clock time. Returns None if there is no such slot
    within CRON_LOOKBACK_DAYS.

    Same naive-then-localise shape as last_anchored_occurrence, and for the
    same reason: `0 8 * * *` means 08:00 local on both sides of a DST shift,
    not a fixed number of elapsed hours apart. Walking days backwards rather
    than solving for the slot keeps the day rule below readable; the cost is
    one iteration per day since the last match, which for any schedule a
    person would actually pick is zero or one."""
    (minutes, hours, doms, months, dows), fields = parse_cron(expr)
    dom_restricted, dow_restricted = fields[2] != "*", fields[4] != "*"
    naive_now = now_utc.astimezone(OSLO).replace(tzinfo=None, second=0, microsecond=0)
    day = naive_now.date()
    for offset in range(CRON_LOOKBACK_DAYS):
        if cron_day_matches(day, doms, months, dows, dom_restricted, dow_restricted):
            today = offset == 0
            for hour in sorted(hours, reverse=True):
                if today and hour > naive_now.hour:
                    continue
                for minute in sorted(minutes, reverse=True):
                    if today and hour == naive_now.hour and minute > naive_now.minute:
                        continue
                    slot = datetime(day.year, day.month, day.day, hour, minute)
                    return slot.replace(tzinfo=OSLO).astimezone(timezone.utc)
        day -= timedelta(days=1)
    return None


def schedule_minutes(schedule):
    """How long an `every@` schedule waits between runs, in minutes, or `None`.

    `None` for every other schedule kind and for anything unparseable: a
    `cron@` or `daily@` heartbeat has no single interval, and neither does
    `every@abc`, so there is no honest number to return and the caller has
    to say what it wants to do about that.

    Split out of `schedule_due` because a second caller needs the same
    number for an unrelated reason. `cycle_health` measures a silent loop
    in heartbeat intervals and had been reading a module constant to do it
    -- a constant that has been wrong twice, because the cadence is
    the owner's to change and he has changed it four times since 2026-08-08.
    One definition, two callers, same reason as `cycle_health.gaps_between`.

    Zero and negative are `None` rather than themselves. `every@0m` is not
    a schedule, and the two callers fail differently and badly on it: the
    scheduler would report it due on every pass of the poll loop, and
    `stalled_for` divides by it.
    """
    if not schedule or not schedule.startswith("every@"):
        return None
    amount, _, _anchor = schedule[len("every@"):].partition("@")
    try:
        value = int(amount[:-1])
    except ValueError:
        return None
    if value <= 0:
        return None
    # Anything that is not an explicit `m` is hours, which is what
    # `schedule_due` has always done -- `every@6h` and `every@6x` are the
    # same schedule to it, and this is not the change that fixes that.
    return value if amount[-1] == "m" else value * 60


def schedule_due(schedule, last_run_iso, created_iso, now_utc):
    """Idempotent due computation — no in-memory scheduler state, so a
    runner restart can never double-fire (critique on Decisions/0006).
    createdAt floors the first run: a daily@08:00 heartbeat created at
    14:00 waits for tomorrow instead of surprise-firing at creation."""
    floor = parse_iso(last_run_iso or created_iso)
    if schedule.startswith("cron@"):
        try:
            occurrence = last_cron_occurrence(schedule[len("cron@"):], now_utc)
        except ValueError:
            # Agora validates at the route, so this is unreachable through the
            # API -- but a hand-edited heartbeat file must not take the whole
            # poll loop down with it and stop every OTHER heartbeat running.
            return False
        return occurrence is not None and occurrence > floor
    if schedule.startswith("every@"):
        minutes = schedule_minutes(schedule)
        if minutes is None:
            # Same hazard and same answer as the cron@ guard above: a
            # hand-edited schedule must not take the poll loop down and
            # stop every OTHER heartbeat. This branch used to raise
            # ValueError out of the loop on `every@abc` and report
            # `every@0m` due on every pass; not firing is the safe
            # direction for both.
            return False
        _amount, _, anchor = schedule[len("every@"):].partition("@")
        delta = timedelta(minutes=minutes)
        if anchor:
            return last_anchored_occurrence(anchor, delta, now_utc) > floor
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
    """Provider-ready history: The owner → user; personas → assistant, other
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


def pending_user_turn(history):
    """The trailing user content of a merged history, or None if the
    thread doesn't end on one — i.e. "the owner spoke last and nobody has
    answered him yet". Used by run_heartbeat to decide whether the
    synthetic trigger should carry his real words along with it."""
    if history and history[-1]["role"] == "user":
        return history[-1]["content"]
    return None


def build_system(persona, conversation=None, heartbeat_extra=None):
    parts = [persona.get("personality") or "You are a helpful assistant."]
    shared = (persona.get("sharedMemory") or "").strip()
    if shared:
        parts.append(f"## Your persistent memory\n{shared}")
    if conversation:
        memory = (conversation.get("memory") or "").strip()
        if memory:
            parts.append(f"## Conversation notes\n{memory}")
    # A "## Participants" roster naming the other personas and teaching the
    # @mention convention used to be built here when `participants` held
    # more than one name. Agora refuses a second persona (agora#67), so it
    # never fired again -- and if it somehow did, it would be telling a
    # persona to @mention names that can no longer be in the thread.
    caps = persona.get("capabilities") or {}
    if caps.get("vaultRead") or caps.get("vaultWrite"):
        vault = [
            "## Vault access\nYou have tools for Edvard's Obsidian vault. Paths are "
            "case-insensitive; folders end with '/'. save_memory REPLACES your entire "
            "persistent memory — always include everything you still want to keep."
        ]
        # The eight query tools below are gated on vaultRead in
        # client_tool_schemas, while this whole section fires on either
        # capability -- so naming them unconditionally promised a write-only
        # persona eight tools it does not have. See
        # tests/test_system_prompt_matches_tools.py.
        if caps.get("vaultRead"):
            vault.append(
                "Beyond single-file read/write you also have vault_search (full-text), "
                "vault_query_frontmatter, vault_validate_frontmatter_schema, "
                "vault_find_stub_notes, vault_find_duplicate_titles, vault_get_token_metrics, "
                "vault_git_revision_history and vault_summarize_recent_agent_work (both "
                "against the daily backup mirror on GitHub)"
                + (", and vault_update_frontmatter_batch for bulk metadata edits" if caps.get("vaultWrite") else "")
                + "."
            )
        elif caps.get("vaultWrite"):
            vault.append(
                "You also have vault_update_frontmatter_batch for bulk metadata edits."
            )
        parts.append(" ".join(vault))
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
                "name that reflects the actual change, not a generic one. You also "
                "have github_comment — posts a comment on an issue or a PR by number "
                "(both share one numbering space, so one tool covers each)."
            )
        if caps.get("githubMerge"):
            gh_parts.append(
                "You have merge_pr — merges an open PR, but only once every check-run "
                "on it is green. It refuses otherwise; there is no override."
            )
        parts.append(" ".join(gh_parts))
    if caps.get("terminalExec"):
        parts.append(
            "## Terminal access\nYou have terminal_exec — an unrestricted shell "
            "(bash -lc) in this runner pod, no command allowlist unlike your other "
            "tools. Use it to inspect or fix anything no purpose-built tool covers "
            "yet, or to run git/npm/python/etc. directly. Commands run in a "
            "per-pod scratch workspace that persists between calls but not across "
            "a pod restart. This is your highest-blast-radius tool — prefer a "
            "narrower tool when one already does the job."
        )
    if heartbeat_extra:
        parts.append(heartbeat_extra)
    return "\n\n".join(parts)
