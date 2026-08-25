"""What `tools.secret_scan` must not get wrong.

Three properties, each pinned because getting it wrong produces a report
that *looks* right: a clean answer from a scanner that never matched, a
checkout that is skipped rather than scanned, and a real secret excused by
the filter that exists to excuse the encrypted ones.
"""

import os

from tools import secret_scan


SEALED = """apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: agents-claude-auth
spec:
  encryptedData:
    token: AgBXQF6easkaZZgo1111111111111111111111111111
"""

PLAINTEXT = """apiVersion: v1
kind: Secret
metadata:
  name: oops
stringData:
  token: AgBXQF6easkaZZgo1111111111111111111111111111
"""


def test_a_canary_that_does_not_fire_is_reported_as_no_instrument(monkeypatch, capsys):
    """The whole contract: an unproven scanner never yields a clean report.

    A `gitleaks` that scans nothing and a genuinely clean repo print the
    same thing and exit the same way, so the only difference this tool can
    stand on is whether it watched the scanner catch something first.
    """
    monkeypatch.setattr(secret_scan, "find_scanner", lambda **_: ("/bin/true", "faked"))
    monkeypatch.setattr(secret_scan, "prove_scanner", lambda _scanner: set())
    monkeypatch.setattr(secret_scan, "scan_dir", lambda *_: [])

    assert secret_scan.main(["--repo", "/tmp"]) == 1
    printed = capsys.readouterr().out
    assert "NO INSTRUMENT" in printed
    # Not merely non-zero: the text must not read as a clean sweep.
    assert "Nothing to act on" not in printed


def test_a_partly_firing_canary_is_still_no_instrument(monkeypatch, capsys):
    """Both rules, not either. One match proves one rule, not the scanner."""
    monkeypatch.setattr(secret_scan, "find_scanner", lambda **_: ("/bin/true", "faked"))
    monkeypatch.setattr(secret_scan, "prove_scanner", lambda _s: {"aws-access-token"})
    monkeypatch.setattr(secret_scan, "scan_dir", lambda *_: [])

    assert secret_scan.main(["--repo", "/tmp"]) == 1
    assert "github-pat" in capsys.readouterr().out


def test_the_canary_body_carries_two_contiguous_credentials():
    """The bug the canary caught in its own first draft.

    Version one split each key across two string literals *in the text it
    wrote*, so the temporary file said `"AKIA" "QYLP…"` and no rule could
    ever match it. The proof would have shipped unable to fail.
    """
    for prefix, length in (("AKIA", 16), ("ghp_", 36)):
        start = secret_scan.CANARY_BODY.index(prefix) + len(prefix)
        run = secret_scan.CANARY_BODY[start : start + length]
        assert run.isalnum(), "%s canary is not contiguous: %r" % (prefix, run)
        assert len(run) == length


def test_a_worktree_checkout_is_found(tmp_path):
    """`.git` is a file, not a directory, in every cycle's own workspace.

    `os.path.isdir` here returned no checkouts at all and printed it as an
    empty workspace, which reads as "nothing to scan" rather than as a
    wrong predicate.
    """
    worktree = tmp_path / "agora-persona-runner"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
    ordinary = tmp_path / "platform-config"
    (ordinary / ".git").mkdir(parents=True)
    (tmp_path / "not-a-repo").mkdir()

    found = {os.path.basename(p) for p in secret_scan.workspace_repos(str(tmp_path))}
    assert found == {"agora-persona-runner", "platform-config"}


def test_sealed_secrets_are_excused_and_plaintext_ones_are_not(tmp_path):
    """The filter matches on `kind`, so a plaintext Secret beside the
    sealed ones is still a finding. Excusing by directory would have hidden
    the one file in `secrets/sealed/` that actually mattered."""
    sealed = tmp_path / "sealed.yaml"
    sealed.write_text(SEALED)
    plain = tmp_path / "plain.yaml"
    plain.write_text(PLAINTEXT)

    excused = secret_scan.sealed_secret_files([str(sealed), str(plain)])
    assert excused == {str(sealed)}


def test_excused_findings_are_counted_not_silently_dropped(tmp_path, monkeypatch):
    """A filter that hides its own count reads as "there was nothing"."""
    sealed = tmp_path / "sealed.yaml"
    sealed.write_text(SEALED)
    monkeypatch.setattr(secret_scan, "git_ignored", lambda *_: set())

    kept, skipped = secret_scan.filter_findings(
        str(tmp_path),
        [{"File": str(sealed), "RuleID": "generic-api-key"},
         {"File": str(tmp_path / "real.py"), "RuleID": "aws-access-token"}],
    )
    assert skipped == 1
    assert [f["RuleID"] for f in kept] == ["aws-access-token"]


def test_a_reviewer_worktree_is_not_a_checkout(tmp_path):
    """The half of the predicate I re-derived and got wrong.

    A `_review-*` directory is a reviewer's scratch worktree, so `.git` is a
    file there too. My own version of this discovery excluded nothing and
    would have scanned them as repos — which is why the tool now asks
    `tidy_workspace`, which learned that rule the expensive way, instead of
    asking a copy of it that only knows what bit me.
    """
    for name in ("agora", "_review-c411"):
        (tmp_path / name).mkdir()
        (tmp_path / name / ".git").write_text("gitdir: /elsewhere\n")

    found = {os.path.basename(p) for p in secret_scan.workspace_repos(str(tmp_path))}
    assert found == {"agora"}


def test_both_workspace_roots_are_swept(tmp_path, monkeypatch):
    """A concurrent cycle has a private root and a shared one.

    Sweeping only `NOVA_WORKSPACE` scanned four checkouts and printed
    "clean" without a word about the four it never opened.
    """
    private, shared = tmp_path / "mine", tmp_path / "shared"
    for root, name in ((private, "agora"), (shared, "platform-config")):
        (root / name).mkdir(parents=True)
        (root / name / ".git").write_text("gitdir: /elsewhere\n")
    monkeypatch.setattr(
        secret_scan.tidy_workspace, "workspace_roots", lambda: [str(private), str(shared)]
    )

    found = {os.path.basename(p) for p in secret_scan.workspace_repos()}
    assert found == {"agora", "platform-config"}
