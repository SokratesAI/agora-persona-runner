"""The system prompt may not describe a tool the persona was not given.

Why this exists (2026-08-06): `turns.py:build_system` writes a prose
description of the persona's tools, and `tools_schemas.py:client_tool_schemas`
decides which tools actually exist. Both are built from the same `caps`
dict and were free to disagree forever, because for one provider only one
of them was ever executed.

That is not hypothetical -- it is how agora-persona-runner#48 happened.
`providers/claude_cli.py` had no client-side tool loop at all, so a
claude-cli persona had *none* of these tools, while its own system prompt
listed every one of them. Nova read "You have merge_pr -- merges an open
PR, but only once every check-run on it is green. It refuses otherwise;
there is no override" at the top of its context for five days and merged
with a raw `gh pr merge`, which has no such guard. Nothing detected it;
Edvard found it from the outside, from a parenthetical in a digest.

#48 made the two agree for the capability sets that exist today. These
tests are what stops them drifting apart again, and they immediately
found a second, latent instance of the same bug: the vault section fires
on `vaultRead or vaultWrite`, but the eight query tools it names are
gated on `vaultRead` alone, so a write-only persona was promised eight
tools it did not have. No persona is configured that way today, which is
exactly why nobody would have noticed the day one was.

The prompt is allowed to stay SILENT about a tool the persona has -- the
roster is passed to the model separately and a description is a courtesy.
The reverse is what does damage: an instruction to use something that
isn't there, which the model cannot discover is false.
"""
import itertools
import re

import pytest

from agora_runner.tools_schemas import client_tool_schemas
from agora_runner.turns import build_system

CAPABILITIES = (
    "vaultRead",
    "vaultWrite",
    "kubectlRead",
    "githubRead",
    "manageAgora",
    "githubWrite",
    "githubMerge",
    "terminalExec",
    "webSearch",
)

# A tool name as it appears in prose: lowercase snake_case with at least one
# underscore. Deliberately not an allowlist -- an allowlist would have to be
# updated by the same person who adds the drift, which is how the original
# bug survived. Verified to match tool names and nothing else: the persona
# text below is neutral on purpose, so every identifier in the built prompt
# comes from the capability sections.
TOOL_NAME_IN_PROSE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _persona(caps):
    return {
        "name": "Test",
        "personality": "You are a test persona.",
        "capabilities": caps,
        "id": "00000000-0000-0000-0000-000000000000",
    }


def _tools_named_in_prompt(caps):
    return set(TOOL_NAME_IN_PROSE.findall(build_system(_persona(caps))))


def _tools_actually_granted(caps):
    return {schema["name"] for schema in client_tool_schemas(caps)}


def _all_capability_combinations():
    for size in range(len(CAPABILITIES) + 1):
        for combo in itertools.combinations(CAPABILITIES, size):
            yield {cap: True for cap in combo}


def test_every_tool_the_prompt_names_is_a_real_tool():
    """Catches a prompt describing a tool that was never built, or renamed."""
    every_tool = _tools_actually_granted({cap: True for cap in CAPABILITIES})
    named = _tools_named_in_prompt({cap: True for cap in CAPABILITIES})

    assert named, "the scan found no tool names at all -- the regex has rotted"
    assert named <= every_tool, (
        "build_system names tools that client_tool_schemas never produces: "
        f"{sorted(named - every_tool)}"
    )


@pytest.mark.parametrize("caps", list(_all_capability_combinations()))
def test_prompt_never_promises_a_tool_the_persona_lacks(caps):
    """The #48 bug, for every one of the 512 capability combinations."""
    promised = _tools_named_in_prompt(caps)
    granted = _tools_actually_granted(caps)

    assert promised <= granted, (
        f"capabilities {sorted(caps)} produce a system prompt promising "
        f"{sorted(promised - granted)}, which client_tool_schemas does not grant"
    )


def test_the_guard_catches_a_prompt_that_lies():
    """The test above is only worth having if it can actually fail.

    Asserts against the real known-bad shape rather than a mock: the eight
    read-only query tools are gated on vaultRead, so naming them under a
    write-only persona is precisely the drift this file exists to stop.
    """
    write_only = {"vaultWrite": True}
    granted = _tools_actually_granted(write_only)

    assert "vault_search" not in granted, (
        "vault_search is no longer vaultRead-gated -- this test's premise is "
        "stale and it is silently no longer checking anything"
    )
    assert _tools_named_in_prompt(write_only) <= granted
