"""A chat turn is told it can build a demo -- but only when it has the shell.

Idea #137. The two halves of "ask for a demo in the chat dock" both already
existed and had never been joined: the chat dock on every Nova page is a live
conversation answered by a Claude Code CLI session on the bridge pod, and
`tools.demo` has been able to start, serve and stop a demo since idea #135.
Nothing in the system prompt said so, so a turn asked for a demo had no way
to discover it could make one.

The gate is a *provider* question, not a capability checkbox, which is why it
cannot live in the `caps` sections: only `claude-cli:` runs the turn inside a
CLI session on the bridge pod. An `anthropic:` or `gemini:` persona reaches
the world through Agora's capability tools alone and has no shell anywhere,
so telling it to run `python3 -m tools.demo` is exactly the
agora-persona-runner#48 failure -- an instruction to use something that isn't
there, which the model cannot discover is false.
"""
import pytest

from agora_runner.nova_demos import DURABLE_ROOT, PUBLIC_BASE, concurrent_root
from agora_runner.turns import DEMO_SECTION, build_system, runs_on_the_bridge

HEADING = "## Live demos"


def _persona(**over):
    persona = {
        "name": "Nova",
        "personality": "You are Nova.",
        "capabilities": {},
        "model": "claude-cli:claude-opus-5",
    }
    persona.update(over)
    return persona


def test_a_bridge_session_is_told_it_can_build_a_demo():
    assert HEADING in build_system(_persona())


@pytest.mark.parametrize("model", [
    "anthropic:claude-opus-5",
    "gemini:gemini-3-pro",
    "",
])
def test_a_persona_without_the_bridge_shell_is_not_told(model):
    """#48's shape: never describe a tool this persona cannot reach."""
    assert HEADING not in build_system(_persona(model=model))


def test_a_persona_with_no_model_key_at_all_is_not_told():
    persona = _persona()
    del persona["model"]
    assert HEADING not in build_system(persona)


def test_a_restricted_cli_persona_is_not_told():
    """`claudeCliRestricted` asks the bridge for its full tool denylist,
    which takes the shell away -- so the instruction would be a lie again."""
    assert not runs_on_the_bridge(_persona(claudeCliRestricted=True))
    assert HEADING not in build_system(_persona(claudeCliRestricted=True))


def test_it_is_not_gated_on_any_capability_checkbox():
    """A demo is built with the CLI's own Bash, not with an Agora tool, so
    a persona with no capabilities at all still gets it."""
    assert HEADING in build_system(_persona(capabilities={}))


def test_it_names_the_durable_root_and_never_the_per_turn_one():
    """The trap idea #135 paid for twice: a dev server whose directory the
    bridge deletes at the end of the turn keeps answering, and serves the
    hole. Naming the wrong root here would re-create it by instruction."""
    assert DURABLE_ROOT in DEMO_SECTION
    assert concurrent_root({}) not in DEMO_SECTION


def test_the_url_it_hands_out_is_the_one_the_tool_prints():
    """The whole reason `PUBLIC_BASE` moved into `nova_demos`: a hostname
    written down in three places is a hostname that rots in two of them."""
    from tools.demo import DEMO_BASE, PUBLIC_BASE as tool_base

    assert tool_base is PUBLIC_BASE
    assert DEMO_BASE is PUBLIC_BASE
    assert PUBLIC_BASE + "/<slug>/" in DEMO_SECTION


def test_it_tells_the_turn_to_reply_with_the_link():
    """The acceptance line on idea #135 is him tapping a link. A demo that
    is started and not handed over meets nothing."""
    assert "reply with that link" in DEMO_SECTION
