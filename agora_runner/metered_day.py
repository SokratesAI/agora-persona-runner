"""What the metered Anthropic API has billed today, kept where a restart cannot lose it.

`ANTHROPIC_TURN_TOKEN_CEILING` bounds one turn. It does not bound a day: a
tester who runs ten long turns bills ten ceilings, and the balance this
protects is one prepaid account shared by every persona (idea #249). A
per-day ceiling needs the count kept across turns and across pod restarts, so
it lives in a vault document rather than in this process.

The count is one number per Oslo calendar day, summed over every persona,
because the balance it guards is summed the same way. A read that fails falls
back to what this process has counted itself -- never to zero, which would
reopen the day the moment the vault hiccups -- and a write that fails is
logged and dropped: a turn that has already been billed must still get its
answer.
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from agora_runner.log import log
from agora_runner.vault import vault_read_path_rev, vault_write_path

PATH = "projects/sokrates/projects/agora/nova/resources/metered-spend.json"

OSLO = ZoneInfo("Europe/Oslo")

# Two writers are two runner Pods mid-rollout; a second attempt from a fresh
# read settles a conflict, and a loop would only add latency to a reply.
ATTEMPTS = 2

# What this process has counted, per day. Only the fallback when the vault
# cannot be read.
_local = {}


def today():
    return datetime.now(OSLO).strftime("%Y-%m-%d")


def _parse(content):
    try:
        days = json.loads(content or "{}").get("days")
    except (ValueError, AttributeError):
        return {}
    return days if isinstance(days, dict) else {}


def spent_today():
    """Tokens billed on the metered API so far today, across every persona."""
    day = today()
    try:
        content, _rev = vault_read_path_rev(PATH)
    except Exception as exc:
        log(f"metered_day: could not read {PATH} ({exc}); using this pod's own count")
        return _local.get(day, 0)
    stored = int(_parse(content).get(day) or 0)
    return max(stored, _local.get(day, 0))


def add(tokens):
    """Add a turn's billed tokens to today's total. Never raises."""
    if tokens <= 0:
        return
    day = today()
    _local[day] = _local.get(day, 0) + tokens
    for _attempt in range(ATTEMPTS):
        try:
            content, rev = vault_read_path_rev(PATH)
            days = _parse(content)
            days[day] = int(days.get(day) or 0) + tokens
            # A week is enough to see a pattern; older days only grow the file.
            keep = sorted(days)[-7:]
            body = json.dumps({"days": {d: days[d] for d in keep}}, indent=1) + "\n"
            result = vault_write_path(PATH, body, if_rev=rev, allow_shrink=True)
        except Exception as exc:
            result = f"FAILED({exc})"
        if result == "written":
            return
        log(f"metered_day: write attempt {_attempt + 1} of {tokens} tokens: {result}")
