"""generate_reply -- the one entry point every caller uses, dispatching by model provider."""

from agora_runner.providers.anthropic import anthropic_generate
from agora_runner.providers.gemini import gemini_generate_with_fallback
from agora_runner.providers.claude_cli import claude_cli_generate


def generate_reply(persona, caps, system, history, conversation_id, model_override=None, sticky=False,
                    on_text=None, active_step=None, on_thinking=None):
    model = model_override or persona.get("model") or ""
    provider, _, model_id = model.partition(":")
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
