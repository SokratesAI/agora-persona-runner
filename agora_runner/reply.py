"""generate_reply -- the one entry point every caller uses, dispatching by model provider."""

from agora_runner.config import ALLOW_METERED_UNATTENDED
from agora_runner.providers.anthropic import anthropic_generate
from agora_runner.providers.gemini import gemini_generate_with_fallback
from agora_runner.providers.claude_cli import claude_cli_generate

# 2026-08-10, the owner in issues.md: "Hard rule! We must never use the metered
# api for other than testing! It is expensive and I only have 16$ left. You
# can ofcourse use it for testing and research, but never implement a
# functionality that depends on it that will drain it. We only use the
# subscription based model for production code!"
#
# `anthropic:` is the raw Messages API, billed per token against that
# prepaid balance. `claude-cli:` reaches the same models through the
# subscription and costs nothing per token — and the catalog is a strict
# one-to-one mapping (agora's MODEL_CATALOG), so a blocked persona always
# has an identical twin to move to and never loses a model.
#
# What can actually empty the account is an *unattended* turn: a heartbeat
# or a workflow step runs on a schedule, forever, with nobody watching. A
# person typing in the app is bounded by the person, and he explicitly kept
# testing and research allowed — so this guards the automation and
# deliberately does not block an attended turn.
METERED_PROVIDERS = ("anthropic",)


class MeteredProviderBlocked(RuntimeError):
    """An unattended turn tried to spend the prepaid metered API balance."""


# `unattended` defaults to True, and the default is the whole guard.
#
# It defaulted to False until 2026-08-31, which meant the protection was
# opt-in at every call site: `heartbeats.py` and `workflows.py` each pass
# `unattended=True` and a test asserts they do, but a *fifth* call site
# added by a later cycle would have spent the prepaid balance on a schedule
# and nothing would have said so. That is the shape idea #85 is about --
# spend enforced rather than remembered -- and a guard whose coverage
# depends on every future author recalling one keyword argument is the
# remembered kind.
#
# Defaulting closed inverts who has to remember: a new call site is guarded
# until it argues its way out, and the two paths that genuinely have a
# person behind them (`conversations.py`, a human typing in Agora, and
# `/invoke`, the Ask and Preview boxes) now say `unattended=False` out loud
# next to the reason. Failing this direction is cheap in the other
# direction too -- a call site wrongly marked unattended still runs every
# `claude-cli:` and `gemini:` model exactly as before; the only thing it
# loses is the ability to spend money.
def generate_reply(persona, caps, system, history, conversation_id, model_override=None, sticky=False,
                    on_text=None, active_step=None, on_thinking=None, unattended=True):
    model = model_override or persona.get("model") or ""
    provider, _, model_id = model.partition(":")
    if unattended and provider in METERED_PROVIDERS and not ALLOW_METERED_UNATTENDED:
        raise MeteredProviderBlocked(
            f"refusing an unattended turn on metered provider {provider!r} (model {model!r}): "
            f"it would spend the prepaid API balance on a schedule. Switch this persona to "
            f"'claude-cli:{model_id}', or set ALLOW_METERED_UNATTENDED=1 to override."
        )
    if not history:
        raise RuntimeError("empty history after normalization")
    if provider == "anthropic":
        return anthropic_generate(model_id, bool(persona.get("thinking")), system, history,
                                  caps, persona, conversation_id, on_text=on_text, active_step=active_step,
                                  on_thinking=on_thinking)
    if provider == "gemini":
        return gemini_generate_with_fallback(model_id, bool(persona.get("thinking")), system, history,
                               caps, persona, conversation_id, sticky, on_text=on_text, active_step=active_step,
                               on_thinking=on_thinking)
    if provider == "claude-cli":
        return claude_cli_generate(model_id, bool(persona.get("thinking")), system, history,
                                    caps, persona, conversation_id, on_text=on_text, active_step=active_step,
                                    on_thinking=on_thinking)
    raise ValueError(f"unknown model provider {provider!r}")
