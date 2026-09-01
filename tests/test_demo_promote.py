"""Promoting a demo to a real service -- idea #138, "keep this" in one tap.

The thing worth testing here is not that a claim renders. It is that
**nothing opens a pull request the owner can tap and which then does
nothing.** A promotion PR is a tap in a meeting; every way it can be
opened against a repo that already exists, a name the XRD would refuse,
or a demo whose source is gone is a way of handing him a dead button.
So the tests go at the refusals, and at the one three-state answer that
would otherwise collapse into a wrong yes.
"""

import json
import os
from unittest.mock import patch

import pytest
import yaml

from agora_runner.nova_demos import (
    DemoError,
    check_promotable,
    claim_path,
    promotion_branch,
    promotion_claim,
)

ENTRY = {"slug": "bakeoff", "host": "10.42.0.56", "port": 5174,
         "dir": "/data/workspace/demos/bakeoff", "started_at": "2026-08-27T21:14:02"}


def _args(**kw):
    base = {"slug": "bakeoff", "name": "", "description": "", "dry_run": False}
    base.update(kw)
    return type("A", (), base)()


# --- what may be promoted --------------------------------------------------

def test_the_directory_is_read_from_the_key_the_registry_actually_writes():
    """`register` writes `dir`; the first version of this read `directory`.

    Nothing in the registry has ever carried a `directory` key, so that
    version refused every real demo with a sentence blaming whatever had
    registered it -- a check that fails closed on 100% of valid input.
    """
    assert check_promotable(ENTRY, "bakeoff") == "/data/workspace/demos/bakeoff"


@pytest.mark.parametrize("name", ["Bakeoff", "-bakeoff", "bake_off", "bakeoff-", ""])
def test_a_name_the_xrd_would_refuse_is_refused_here(name):
    """The XRD's pattern, applied before the PR rather than on apply.

    A claim that fails to apply fails minutes after the tap, in a
    Crossplane condition nobody is reading.
    """
    with pytest.raises(DemoError):
        check_promotable(ENTRY, name)


def test_a_name_too_long_for_a_kubernetes_object_is_refused():
    with pytest.raises(DemoError):
        check_promotable(ENTRY, "b" * 64)


def test_a_demo_with_no_source_recorded_cannot_be_promoted():
    with pytest.raises(DemoError):
        check_promotable(dict(ENTRY, dir=""), "bakeoff")


def test_an_unregistered_slug_is_refused():
    with pytest.raises(DemoError):
        check_promotable(None, "bakeoff")


# --- what the claim says ---------------------------------------------------

def test_the_claim_parses_and_asks_for_the_service_by_name():
    doc = yaml.safe_load(promotion_claim(
        "bakeoff", "A bake-off", "https://x/demo/bakeoff/", "/d", "2026-08-27"))
    assert doc["kind"] == "GitHubService"
    assert doc["apiVersion"] == "platform.sokratesai.io/v1alpha1"
    assert doc["metadata"]["name"] == "bakeoff"
    assert doc["metadata"]["namespace"] == "platform-catalog"
    assert doc["spec"]["serviceName"] == "bakeoff"


def test_a_description_with_a_line_break_cannot_break_the_document():
    """The description is a folded scalar with one indented line under it.

    A raw newline would end the scalar and the remainder would parse as
    YAML at the wrong indentation -- either a refused claim or, worse, one
    that applies as something else. Built as the failure, not as a happy
    path: this exact text round-trips wrong if the collapse is removed.
    """
    doc = yaml.safe_load(promotion_claim(
        "bakeoff", "one\nvisibility: public\nkind: Secret",
        "https://x/", "/d", "2026-08-27"))
    assert doc["kind"] == "GitHubService"
    assert doc["spec"]["serviceName"] == "bakeoff"
    assert "\n" not in doc["spec"]["description"]
    assert doc["spec"].get("visibility") is None


def test_visibility_is_left_at_the_schema_default():
    """Promotion never makes a repo public. Public cannot be undone."""
    doc = yaml.safe_load(promotion_claim(
        "bakeoff", "x", "https://x/", "/d", "2026-08-27"))
    assert "visibility" not in doc["spec"]


def test_the_claim_path_and_branch_are_named_after_the_service():
    assert claim_path("bakeoff") == "crossplane/service-bakeoff.yaml"
    assert promotion_branch("bakeoff") == "nova/promote-bakeoff"


# --- what promote refuses to do -------------------------------------------

def _promote(monkeypatch, tmp_path, exists, args=None, claim_exists=False):
    from tools import demo as demo_cli

    checkout = tmp_path / "platform-config"
    (checkout / "crossplane").mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: elsewhere")
    if claim_exists:
        (checkout / "crossplane" / "service-bakeoff.yaml").write_text("x")
    monkeypatch.setenv("NOVA_WORKSPACE", str(tmp_path))
    calls = []

    def _run(argv, **kw):
        calls.append(argv)
        raise AssertionError(f"promote ran a command it should not have: {argv}")

    with patch.object(demo_cli, "_read_registry",
                      lambda: ({"demos": [ENTRY]}, "/tmp/f.rev")), \
         patch.object(demo_cli, "_gh_repo_exists", lambda repo: exists), \
         patch.object(demo_cli, "_run", _run):
        return demo_cli.cmd_promote(args or _args()), calls


def test_a_repo_that_already_exists_is_refused_rather_than_promoted_twice(
        monkeypatch, tmp_path, capsys):
    code, calls = _promote(monkeypatch, tmp_path, exists=True)
    assert code == 2
    assert calls == []
    assert "already exists" in capsys.readouterr().err


def test_github_failing_to_answer_is_not_read_as_the_repo_being_absent(
        monkeypatch, tmp_path, capsys):
    """The three-state check, which is the whole reason it is three-state.

    A `gh` call that errors returning False would open a claim for a
    repository that is already there -- a negative result guaranteed in
    advance by the failure itself.
    """
    code, calls = _promote(monkeypatch, tmp_path, exists=None)
    assert code == 1
    assert calls == []
    assert "could not ask GitHub" in capsys.readouterr().err


def test_a_claim_file_already_in_the_checkout_is_refused(
        monkeypatch, tmp_path, capsys):
    code, _ = _promote(monkeypatch, tmp_path, exists=False, claim_exists=True)
    assert code == 2
    assert "already exists" in capsys.readouterr().err


def test_dry_run_prints_the_claim_and_touches_nothing(
        monkeypatch, tmp_path, capsys):
    code, calls = _promote(monkeypatch, tmp_path, exists=False,
                           args=_args(dry_run=True))
    assert code == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "kind: GitHubService" in out
    assert not (tmp_path / "platform-config" / "crossplane"
                / "service-bakeoff.yaml").exists()


def test_a_missing_platform_config_checkout_is_reported_not_guessed(
        monkeypatch, tmp_path, capsys):
    from tools import demo as demo_cli
    monkeypatch.setenv("NOVA_WORKSPACE", str(tmp_path))
    with patch.object(demo_cli, "_read_registry",
                      lambda: ({"demos": [ENTRY]}, "/tmp/f.rev")), \
         patch.object(demo_cli, "_gh_repo_exists", lambda repo: False):
        assert demo_cli.cmd_promote(_args()) == 1
    assert "no platform-config checkout" in capsys.readouterr().err


# --- what ship refuses to do ----------------------------------------------

def test_ship_refuses_while_the_promotion_pr_is_unmerged(tmp_path, capsys):
    """The repo not existing yet is the normal state, not an error to push past."""
    from tools import demo as demo_cli
    src = tmp_path / "src"
    src.mkdir()
    entry = dict(ENTRY, dir=str(src))
    with patch.object(demo_cli, "_read_registry",
                      lambda: ({"demos": [entry]}, "/tmp/f.rev")), \
         patch.object(demo_cli, "_gh_repo_exists", lambda repo: False), \
         patch.object(demo_cli, "_run", lambda *a, **k: pytest.fail("pushed")):
        assert demo_cli.cmd_ship(_args()) == 2
    assert "has not been merged" in capsys.readouterr().err


def test_ship_refuses_when_the_demo_source_is_gone_from_this_pod(tmp_path, capsys):
    from tools import demo as demo_cli
    gone = dict(ENTRY, dir=str(tmp_path / "was-here"))
    with patch.object(demo_cli, "_read_registry",
                      lambda: ({"demos": [gone]}, "/tmp/f.rev")), \
         patch.object(demo_cli, "_run", lambda *a, **k: pytest.fail("ran")):
        assert demo_cli.cmd_ship(_args()) == 2
    assert "nothing to ship" in capsys.readouterr().err


def test_ship_refuses_to_re_init_a_directory_that_is_already_a_repo(
        tmp_path, capsys):
    """`git init` inside an existing checkout would rewrite its history.

    The demo directory is normally a scratch copy, but nothing stops it
    being a real clone, and that is not a mistake to discover from a push.
    """
    from tools import demo as demo_cli
    src = tmp_path / "src"
    src.mkdir()
    entry = dict(ENTRY, dir=str(src))

    class _Done:
        returncode = 0
        stdout = str(src) + "\n"
        stderr = ""

    with patch.object(demo_cli, "_read_registry",
                      lambda: ({"demos": [entry]}, "/tmp/f.rev")), \
         patch.object(demo_cli, "_gh_repo_exists", lambda repo: True), \
         patch.object(demo_cli, "_run", lambda *a, **k: _Done()):
        assert demo_cli.cmd_ship(_args()) == 2
    assert "already inside a git repository" in capsys.readouterr().err


# --- the three-state answer itself ----------------------------------------

@pytest.mark.parametrize("code,err,expected", [
    (0, "", True),
    (1, "GraphQL: Could not resolve to a Repository with the name "
        "'SokratesAI/bakeoff'. (repository)", False),
    (1, "HTTP 404: Not Found", False),
    (1, "dial tcp: lookup api.github.com: no such host", None),
    (1, "HTTP 502 Bad Gateway", None),
    (1, "", None),
])
def test_gh_repo_exists_says_it_could_not_ask_rather_than_guessing(
        code, err, expected):
    """The mutation that survived the first pass.

    Every refusal test above patches `_gh_repo_exists` wholesale, so all of
    them stayed green with the function's `None` branch replaced by
    `False` -- twenty-one green tests over a three-state answer that had
    become two-state, which is the exact shape of a suite that certifies
    the code it is not running. This one goes at the function.
    """
    from tools import demo as demo_cli

    class _Done:
        returncode = code
        stdout = ""
        stderr = err

    with patch.object(demo_cli, "_run", lambda *a, **k: _Done()):
        assert demo_cli._gh_repo_exists("SokratesAI/bakeoff") is expected


def test_promote_refuses_to_move_a_checkout_that_has_uncommitted_work(
        monkeypatch, tmp_path, capsys):
    """`checkout -B` rewrites the working tree a sibling cycle may be in.

    Cycles overlap now, and the platform-config checkout is shared within a
    workspace. Carrying someone else's uncommitted work onto a promotion
    branch -- or losing it to a conflict -- is not a thing to find out from
    a failed `git checkout`.
    """
    from tools import demo as demo_cli

    checkout = tmp_path / "platform-config"
    (checkout / "crossplane").mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: elsewhere")
    monkeypatch.setenv("NOVA_WORKSPACE", str(tmp_path))

    class _Dirty:
        returncode = 0
        stdout = " M crossplane/service-other.yaml\n"
        stderr = ""

    seen = []

    def _run(argv, **kw):
        seen.append(argv)
        if argv[3:5] == ["status", "--porcelain"]:
            return _Dirty()
        raise AssertionError(f"ran past the dirty check: {argv}")

    with patch.object(demo_cli, "_read_registry",
                      lambda: ({"demos": [ENTRY]}, "/tmp/f.rev")), \
         patch.object(demo_cli, "_gh_repo_exists", lambda repo: False), \
         patch.object(demo_cli, "_run", _run):
        assert demo_cli.cmd_promote(_args()) == 2
    assert "uncommitted changes" in capsys.readouterr().err
    assert not (checkout / "crossplane" / "service-bakeoff.yaml").exists()
