"""Every workflow in this repo declares what the GITHUB_TOKEN may do.

A workflow with no `permissions:` block gets the repository default, which on
this org is write on `contents`. That token is handed to every job in the file
-- including `test`, `vault-drift` and `image-changed`, which only check the
repo out and run Python. Two of this repo's three workflows already declared a
block and `build.yaml` did not; nothing here compared them, and the gap was
found by CodeQL (`actions/missing-workflow-permissions`) rather than by a test.

The check is on the *workflow* level rather than per job on purpose. A
job-level block replaces the workflow-level one rather than merging with it,
so a job that genuinely needs `packages: write` (`build-push`) states that for
itself and is unaffected by the floor set here.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.y*ml"))

# The one verb every job here needs: they all check the repository out.
READ_ONLY = "read"


def _load(path: Path) -> dict:
    # `on:` is parsed by PyYAML 1.1 semantics as the boolean True in some
    # loaders; nothing below reads it, so it does not matter which key it lands
    # under. Everything this test asserts is keyed on plain strings.
    return yaml.safe_load(path.read_text())


def test_there_are_workflows_to_judge():
    """A glob that matched nothing would make every test below vacuously true."""
    assert WORKFLOWS, "no workflow files found -- the rest of this file proves nothing"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_declares_permissions(path: Path):
    doc = _load(path)
    permissions = doc.get("permissions")
    assert permissions is not None, (
        f"{path.name} declares no workflow-level `permissions:`, so its GITHUB_TOKEN "
        f"is minted with the repository default (write on contents) for every job. "
        f"Add `permissions:\n  contents: read` above `jobs:`."
    )
    assert isinstance(permissions, dict), (
        f"{path.name}: `permissions:` is {permissions!r}. This test wants the mapping "
        f"form so the individual scopes can be read; `read-all` and `write-all` are "
        f"not what any workflow here means."
    )
    assert permissions.get("contents") == READ_ONLY, (
        f"{path.name}: workflow-level `contents:` is {permissions.get('contents')!r}, "
        f"not {READ_ONLY!r}. A job that needs to write to this repo declares that on "
        f"the job, where it is visible next to the step that does the writing."
    )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_job_silently_inherits_a_write_scope(path: Path):
    """A write scope at workflow level reaches jobs that never asked for one.

    `nova-deadman.yaml` needs `issues: write` and states it at workflow level
    because its only job is the one that opens the issue. That is fine while
    the file has one job; it stops being fine the moment a second job is added,
    and this is what says so.
    """
    doc = _load(path)
    permissions = doc.get("permissions") or {}
    writes = sorted(k for k, v in permissions.items() if v == "write")
    if not writes:
        return
    jobs = doc.get("jobs") or {}
    assert len(jobs) == 1, (
        f"{path.name} grants {writes} at workflow level and has {len(jobs)} jobs "
        f"({sorted(jobs)}). Move the write scope onto the job that needs it."
    )
