"""Environment-derived constants, model catalogs, and capability defaults -- shared by every other module in this package. No internal imports."""

import os
from datetime import timezone

try:
    from zoneinfo import ZoneInfo
    OSLO = ZoneInfo("Europe/Oslo")
except Exception:  # pragma: no cover — image without tzdata
    OSLO = timezone.utc


# Verbose diagnostics (turn decisions, tool dispatch, persistence PATCH
# results, provider request tracing) -- gated behind an env var so normal
# operation doesn't permanently carry the extra log volume. The provider
# error-body logging below (429s, non-200s) is NOT gated by this -- that's
# cheap (only fires on actual failures) and was the single biggest gap in
# diagnosing the 2026-07-23 Gemini fallback investigation: every "rate
# limited (429)" log line up to this point never showed what Google's
# response body actually said, so there was no way to tell a real per-model
# quota block from a request-routing bug from an account-wide throttle.
DEBUG_LOGGING = os.environ.get("DEBUG_LOGGING", "").strip().lower() in ("1", "true", "yes")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
# Escape hatch for the metered-provider guard in reply.py. Off by default:
# an unattended turn (heartbeat, workflow step) may not spend the prepaid
# Anthropic balance. See reply.py's METERED_PROVIDERS comment for Edvard's
# rule and why attended turns are deliberately still allowed.
ALLOW_METERED_UNATTENDED = os.environ.get(
    "ALLOW_METERED_UNATTENDED", "").strip().lower() in ("1", "true", "yes")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
AGORA_URL = os.environ.get("AGORA_URL", "http://agora.agents.svc.cluster.local:8080")
AGORA_INTERNAL_URL = os.environ.get(
    "AGORA_INTERNAL_URL", "http://agora.agents.svc.cluster.local:8081"
)
AGORA_TOKEN = os.environ.get("AGORA_TOKEN", "")
CLAUDE_BRIDGE_URL = os.environ.get(
    "CLAUDE_BRIDGE_URL", "http://agora-claude-bridge.agents.svc.cluster.local:8090"
)
CLAUDE_BRIDGE_TOKEN = os.environ.get("CLAUDE_BRIDGE_TOKEN", "")
# Let a claude-cli turn run alongside one already in flight, instead of
# blocking on the bridge's process-wide invocation lock. Off by default:
# with it off, a heartbeat that arrives while another is running waits its
# turn, which is exactly what happens today and is safe. With it on, both
# run, and each gets its own git worktree (bridge `_provision_workspace`).
# This is the switch for Edvard's 18-minute cadence -- at 18 minutes an
# average 18-minute cycle overlaps the next one by design, and he asked
# for that overlap rather than a queue. One env var so the flip is a
# config change and the rollback is the same change back.
CLAUDE_CLI_CONCURRENT = os.environ.get("CLAUDE_CLI_CONCURRENT", "").lower() in ("1", "true", "yes")
RUNNER_PORT = int(os.environ.get("RUNNER_PORT", "8082"))
# Nova's read-only site (nova_site.py). Deliberately a different port from
# RUNNER_PORT rather than another path on it: this one is reachable from
# the tailnet, and RUNNER_PORT -- which carries /invoke, /mcp and
# /tool-activity -- must stay cluster-internal. The split is what lets the
# Service, Ingress and NetworkPolicy each name one port and mean it.
NOVA_PORT = int(os.environ.get("NOVA_PORT", "8083"))
# The model that answers a comment on a journal card (nova_replies.py).
# Sonnet rather than whatever a cycle runs on: the turn is one short,
# tool-less reply written from an entry it is handed, and it draws on the
# same subscription quota the cycles do. No provider prefix -- this goes
# straight to the bridge, so the subscription is the only thing it can
# spend (identity.md rule 9).
NOVA_REPLY_MODEL = os.environ.get("NOVA_REPLY_MODEL", "claude-sonnet-5")
# Whose heartbeat gets the journal self-check put in front of it
# (cycle_health.py, wired up in heartbeats.run_heartbeat). The journal
# folder this checks is Nova's alone -- JOURNAL_DIR is already a hardcoded
# Nova path one module over -- so the id is defaulted rather than required.
# A required env var would have made the check inert in production until a
# manifest caught up, which is the exact way the last two conflict-safety
# cycles shipped a capability that nothing called.
NOVA_PERSONA_ID = os.environ.get(
    "NOVA_PERSONA_ID", "08ffac94-7c4a-4506-897f-968c592358cb"
)
# Where the bridge sends live tool-use reports back to -- this process's
# own in-cluster address (tool_activity.py explains why the reports come
# here rather than going straight to Agora's internal API). A default
# rather than required config so the feature works without a manifest
# change; override if the Service is renamed or moves off RUNNER_PORT.
#
# Load-bearing assumption: this points at the Service, so it only comes
# back to the replica that issued the grant because this deployment runs
# exactly one (strategy Recreate, one replica -- and the whole drain design
# in main.py assumes that too). Scaling the runner out would route some
# callbacks to a replica that never minted the token, which fails closed:
# a 401 and a missing chip, not a chip in the wrong conversation.
RUNNER_SELF_URL = os.environ.get(
    "RUNNER_SELF_URL", "http://agora-persona-runner.agents.svc.cluster.local:8082"
)
# The same idea for the site process, and it has to be its own value: a
# journal-card reply is generated by the `nova-site` pod, so the grant it
# mints lives in *that* process's memory and the bridge has to call back
# there, not to the runner. Pointing this at the runner would hand the
# CLI a token the runner has never heard of -- a 401 on every tool call.
# Same single-replica assumption as above, and the site's Deployment is
# also one replica.
NOVA_SITE_SELF_URL = os.environ.get(
    "NOVA_SITE_SELF_URL", "http://nova-site.agents.svc.cluster.local:8083"
)
POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
COUCHDB_URL = os.environ.get("COUCHDB_URL", "http://couchdb.obsidian.svc.cluster.local:5984")
COUCHDB_USER = os.environ.get("COUCHDB_USER", "")
COUCHDB_PASSWORD = os.environ.get("COUCHDB_PASSWORD", "")
COUCHDB_DB = os.environ.get("COUCHDB_DB", "obsidian")
# Nova's own database. Empty (the default) means every path resolves to
# COUCHDB_DB exactly as before, so this file is inert until the migrated
# documents are actually in place — see vault.db_for.
COUCHDB_NOVA_DB = os.environ.get("COUCHDB_NOVA_DB", "")
# Deliberately separate from GH_TOKEN/GITHUB_TOKEN (the broadly-scoped bot
# credential used elsewhere on this platform for repo/PR writes) -- falls
# back to whatever's already in the environment only so the tool degrades
# to a clear error rather than crashing if nothing is configured yet.
GITHUB_READONLY_TOKEN = os.environ.get(
    "GITHUB_READONLY_TOKEN", os.environ.get("GH_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
).strip()
# 2026-07-26: the broadly-scoped bot account token itself, ONLY for
# create_pr/merge_pr (githubWrite/githubMerge) -- every other tool in
# this file deliberately avoids this credential in favor of a narrower
# one (GITHUB_READONLY_TOKEN above, COUCHDB_* for vault, the in-cluster
# ServiceAccount for kubectl_read). Real PR writes have no narrower
# credential to reach for, so the scoping has to live in what the two
# tools are hardcoded to do (see create_pr/merge_pr's docstrings), not
# in the token.
GITHUB_BOT_TOKEN = os.environ.get("GITHUB_BOT_TOKEN", "").strip()
GITHUB_ORG = os.environ.get("GITHUB_ORG", "SokratesAI")
# TinyFish's Search API (Issues #1 revisited again, 2026-07-23): free tier,
# no credits consumed per their docs, real structured JSON results -- used
# in place of both providers' broken/dropped search (see web_search_tinyfish
# below for why DuckDuckGo scraping didn't work out either: it got
# anti-bot-blocked on the very first live query).
TINYFISH_API_KEY = os.environ.get("TINYFISH_API_KEY", "").strip()

# Mirrors src/models.ts supportsThinking:false — Haiku has no thinking
# mode, Fable 5's is always-on and an explicit disable 400s.
ANTHROPIC_NO_THINKING_TOGGLE = {"claude-haiku-4-5-20251001", "claude-fable-5"}

MAX_HISTORY = 20          # messages included in a generation context
FETCH_LIMIT = 40          # ?limit for detail fetches (critique #5)
AI_TURN_CAP = 6           # consecutive automated turns before a persona-to-
                          # persona @mention chain stops (it no longer pauses
                          # the conversation -- see conversations.py)

# Nothing auto-pauses a conversation any more. Edvard, 2026-08-05: *"Please
# turn off the auto pause of conversations as they are just blocking now.
# They belong to the previous architecture, outdated."* A pause needs a manual
# resume from the conversation menu, so a transient provider outage locked him
# out of a thread until he happened to notice the ⏸️ notice.
#
# What the old FAILURE_PAUSE_CAP was actually protecting against is still real:
# a failed turn appends no reply, so the turn-taking rule sees the same "needs
# a reply" state next tick and retries every POLL_INTERVAL_SECONDS forever with
# zero backoff. Found live 2026-07-23 -- two rate-limited Gemini conversations
# retried nonstop for 8+ hours, each retry cascading the entire
# GEMINI_FALLBACK_CHAIN, which is what exhausted every Gemini model's quota
# rather than just the one each conversation was configured for.
#
# Exponential backoff covers that without blocking: from the 3rd consecutive
# failure the wait doubles (1, 2, 4 ... min) and caps at an hour, so a
# conversation failing all day costs ~15 attempts instead of ~2900 -- and it
# recovers by itself the moment the cause clears, with no menu.
FAILURE_BACKOFF_CAP = 3            # consecutive speak() failures before backing off
FAILURE_BACKOFF_SECONDS = 60       # wait after the first backing-off failure
FAILURE_BACKOFF_MAX_SECONDS = 3600 # ceiling on the doubling
TOOL_ROUNDS_MAX = 100     # client-side tool loop bound (Issues.md: bumped 50->100)
VAULT_CONTEXT_CAP = 24000  # chars of injected vault content per heartbeat (critique #8)
# 2026-07-25: a monitoring-style heartbeat (K3s Sentinel) should only post to
# the chat when it actually finds something worth Edvard's attention -- a
# clean/healthy check silently posting "all good" every run is just noise.
# A heartbeat's own prompt opts into this by instructing the model to reply
# with EXACTLY this sentinel (and nothing else) when there's nothing to
# report; run_heartbeat then skips notify()/audit() entirely for that turn
# (still recorded in the heartbeat's own lastResult, just not the chat).
HEARTBEAT_NO_REPORT_SENTINEL = "NO_ISSUES_FOUND"
# Real per-model output ceilings (verified live via the Models API / models.get,
# 2026-07-22) -- always request the model's actual max rather than an arbitrary
# cap, so a genuinely long reply never gets silently cut off. Falls back to the
# lowest known ceiling (Haiku's 64k) for any model not in this table.
ANTHROPIC_MAX_OUTPUT_TOKENS = {
    "claude-haiku-4-5-20251001": 64000,
    "claude-sonnet-5": 128000,
    "claude-opus-4-8": 128000,
    "claude-fable-5": 128000,
}
GEMINI_MAX_OUTPUT_TOKENS = {
    "gemini-flash-latest": 65536,
    "gemini-flash-lite-latest": 65536,
    "gemini-pro-latest": 65536,
    # Pinned snapshots added 2026-07-22 so free-tier personas aren't
    # limited to whatever "-latest" resolves to. See models.ts for which
    # candidates were live-tested and excluded (2.5-tier 404s).
    "gemini-3-flash-preview": 65536,
    "gemini-3.1-flash-lite": 65536,
    "gemini-3.5-flash": 65536,
    "gemini-3.5-flash-lite": 65536,
    "gemini-3.6-flash": 65536,
}

# Best-to-worst ordering used ONLY by the 429 fallback cascade below (Issues
# #2) -- not a general routing preference. A rate-limited turn retries
# starting from the persona's own chosen model's position in this list and
# walks toward the end, so a persona deliberately set to a cheaper/faster
# model is never silently upgraded, only ever degraded further on 429s.
GEMINI_FALLBACK_CHAIN = [
    "gemini-pro-latest",
    "gemini-flash-latest",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
]
GEMINI_LABELS = {
    "gemini-pro-latest": "Gemini Pro",
    "gemini-flash-latest": "Gemini Flash",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3-flash-preview": "Gemini 3 Flash (Preview)",
    "gemini-flash-lite-latest": "Gemini Flash Lite",
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite",
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
}
# Single next-hop fallback per model, derived from GEMINI_FALLBACK_CHAIN's
# order (2026-07-23 redesign, Issues.md #2 revisited): the old design
# cascaded through the whole remaining chain on EVERY turn but never
# remembered the outcome, so a conversation whose primary model was
# rate-limited kept re-attempting that same exhausted model on every single
# poll tick forever -- at a 5s poll interval that alone can exceed a model's
# RPM cap (some Gemini tiers are as low as 5-15 rpm), so the wasted
# first-attempt request perpetuates its own rate limit. Now the winning
# fallback is persisted onto the conversation (see gemini_generate_with_fallback
# below), so subsequent turns start directly at the working model instead of
# re-attempting a known-bad one. Last entry in the chain has no further
# fallback (.get() returns None, which ends the walk).
GEMINI_MODEL_FALLBACK = {
    GEMINI_FALLBACK_CHAIN[i]: GEMINI_FALLBACK_CHAIN[i + 1]
    for i in range(len(GEMINI_FALLBACK_CHAIN) - 1)
}


class GeminiRateLimited(RuntimeError):
    """Raised when a Gemini call returns 429, or a transient infra error
    (503 UNAVAILABLE/"high demand", 500 INTERNAL) -- signals
    gemini_generate_with_fallback to try the next model in
    GEMINI_FALLBACK_CHAIN instead of failing the whole turn (Issues #2).

    2026-07-24: broadened from 429-only after live logs showed 503s
    ("This model is currently experiencing high demand") failing turns
    outright with zero fallback attempt, several times in one session --
    the exact "pauses all the time even though cascading should work"
    complaint. A 503 is exactly the kind of error a DIFFERENT model is
    likely to answer fine, same reasoning as 429; unlike 429 it carries no
    useful quota detail in the body, so there's nothing more to log."""

    def __init__(self, model_id, status=429):
        reason = "rate limited (429)" if status == 429 else f"unavailable ({status})"
        super().__init__(f"gemini {model_id} {reason}")
        self.model_id = model_id
        self.status = status


GEMINI_TRANSIENT_STATUSES = {429, 500, 503}


DEFAULT_CAPS = {
    "webSearch": True,
    "vaultRead": True,
    "vaultWrite": False,
    "codeExecution": False,
    "kubectlRead": False,
    "githubRead": False,
    "manageAgora": False,
    "githubWrite": False,
    "githubMerge": False,
    "terminalExec": False,
    "novaCapture": False,
}
NO_CAPS = {
    "webSearch": False,
    "vaultRead": False,
    "vaultWrite": False,
    "codeExecution": False,
    "kubectlRead": False,
    "githubRead": False,
    "manageAgora": False,
    "githubWrite": False,
    "githubMerge": False,
    "terminalExec": False,
    # Write one bullet into Edvard's issues.md/ideas.md capture list, and
    # nothing else -- the narrow write half of the journal-card reply turn
    # (nova_replies.py). Deliberately not folded into vaultWrite: that cap
    # advertises vault_write/vault_append, which can address any document
    # in the vault, and the whole argument for letting an HTTP-triggered
    # reply write at all is that it can only ever add a line to one of two
    # files it does not choose. Off for every persona; nova_replies is the
    # only caller that turns it on.
    "novaCapture": False,
}
