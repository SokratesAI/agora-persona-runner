"""What a round on the metered Anthropic API cost, in US dollars.

`ANTHROPIC_DAY_TOKEN_CEILING` counts tokens, and a token is not a price: two
million cache reads on Haiku cost about twenty cents, two million output tokens
on Fable cost a hundred dollars, and the balance it guards held sixteen
(identity.md rule 9). This prices each round off the API's own `usage` so the
day can also be capped in the unit the balance is spent in (idea #249).

Rates are USD per million tokens, first-party API, from the published price
list (claude-api skill, cached 2026-06-24). A model this table does not name is
priced at the highest rate in it, so a new model can only ever read as too
expensive, never as free.
"""

# (input, output) USD per million tokens, matched on the longest id prefix.
RATES = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    # Opus 4 and 4.1 were the last models priced at the old Opus rate.
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

UNKNOWN = max(RATES.values())

# Multipliers on the input rate. A cache read is 0.1x on every model but Fable
# 5.1 (0.025x); charging it 0.1x there over-counts, which is the safe side.
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.1


def rates(model_id):
    matches = [prefix for prefix in RATES if model_id.startswith(prefix)]
    return RATES[max(matches, key=len)] if matches else UNKNOWN


def round_usd(model_id, usage):
    """Dollars billed for one response, from its `usage` block."""
    usage = usage or {}
    rate_in, rate_out = rates(model_id)
    written = int(usage.get("cache_creation_input_tokens") or 0)
    # The TTL breakdown is only present when a 1-hour write happened; without
    # it every write is the 5-minute kind.
    written_1h = int((usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0)
    written_1h = min(written_1h, written)
    input_equivalent = (
        int(usage.get("input_tokens") or 0)
        + (written - written_1h) * CACHE_WRITE_5M
        + written_1h * CACHE_WRITE_1H
        + int(usage.get("cache_read_input_tokens") or 0) * CACHE_READ
    )
    return (input_equivalent * rate_in + int(usage.get("output_tokens") or 0) * rate_out) / 1_000_000
