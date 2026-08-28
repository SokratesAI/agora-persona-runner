"""Tests for `tools.pin_drift` (Cycle 561, idea #141).

The two that matter most are the ones written after a wrong answer on a
live run, not before it: a commit-SHA pin read as `major 3` and reported
four majors behind, and a drift finding hidden behind exit 1 because one
unrelated pin had no upstream configured.
"""

import pytest

from tools import pin_drift


def test_version_parts_keeps_absent_components_absent():
    # `v4` is the v4 *line*. Reading it as 4.0.0 would invent a minor gap
    # against v4.9.0, which is exactly where the tag already points.
    assert pin_drift.version_parts("v4") == (4, None, None)
    assert pin_drift.version_parts("1.36.2") == (1, 36, 2)
    assert pin_drift.version_parts("v2.98.0") == (2, 98, 0)
    assert pin_drift.version_parts("not-a-version") is None
    assert pin_drift.version_parts("") is None


@pytest.mark.parametrize(
    "pinned,latest,expected",
    [
        ("v4", "v7.0.1", "major"),
        ("v4", "v4.9.0", "current"),      # a floating major tag is not behind
        ("v1.36.2", "v1.37.0", "minor"),
        ("2.96.0", "v2.98.0", "minor"),
        ("1.2.3", "1.2.9", "patch"),
        ("1.2.3", "1.2.3", "current"),
        ("2.0.0", "1.9.9", "current"),    # ahead is not behind
        ("main", "v7.0.1", None),
    ],
)
def test_gap(pinned, latest, expected):
    assert pin_drift.gap(pinned, latest) == expected


def test_is_sha_separates_a_commit_from_a_tag():
    # The live failure: this SHA was read as major 3 and printed as four
    # majors behind actions/checkout v7.
    assert pin_drift.is_sha("3d3c42e5aac5ba805825da76410c181273ba90b1")
    assert pin_drift.is_sha("043fb46")
    assert not pin_drift.is_sha("v4")
    assert not pin_drift.is_sha("4")
    assert not pin_drift.is_sha("40")           # a legal tag, not a commit
    assert not pin_drift.is_sha("v2.98.0")


def test_interesting_paths_picks_dockerfiles_and_workflows_only():
    paths = [
        "Dockerfile",
        "offbox/Dockerfile",
        "Dockerfile.test",
        ".github/workflows/build.yaml",
        ".github/workflows/docs.yml",
        ".github/dependabot.yml",
        "README.md",
        "src/Dockerfile.md",
    ]
    assert pin_drift.interesting_paths(paths) == [
        ".github/workflows/build.yaml",
        ".github/workflows/docs.yml",
        "Dockerfile",
        "Dockerfile.test",
        "offbox/Dockerfile",
        "src/Dockerfile.md",
    ]


def test_pins_in_dockerfile_reads_the_value_from_the_file():
    text = (
        "FROM python:3.12-slim\n"
        "ARG KUBECTL_VERSION=v1.36.2\n"
        "ARG  GH_CLI_VERSION = 2.96.0\n"
        "ARG NODE_MAJOR=24\n"
        "RUN echo ARG FAKE_VERSION=9.9.9\n"
    )
    pins = pin_drift.pins_in("o/r", "Dockerfile", text)
    assert [(p["what"], p["pinned"]) for p in pins] == [
        ("KUBECTL_VERSION", "v1.36.2"),
        ("GH_CLI_VERSION", "2.96.0"),
    ]
    assert all(p["kind"] == "docker-arg" for p in pins)


def test_pins_in_workflow_reads_uses_lines():
    text = (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: docker/build-push-action@v5.1.0\n"
        "      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020\n"
        "      - uses: ./.github/actions/local\n"
        "      - run: echo uses: fake/action@v9\n"
    )
    pins = pin_drift.pins_in("o/r", ".github/workflows/build.yaml", text)
    assert [(p["what"], p["pinned"]) for p in pins] == [
        ("actions/checkout", "v4"),
        ("docker/build-push-action", "v5.1.0"),
        ("actions/setup-node", "820762786026740c76f36085b0efc47a31fe5020"),
    ]


def _fake_gh(files, releases, trees):
    """A `run` stub answering the three gh calls this tool makes."""
    def run(args):
        route = args[1]
        if "git/trees" in route:
            repo = route.split("repos/")[1].split("/git/")[0]
            return 0, "\n".join(trees.get(repo, [])), ""
        if "/releases/latest" in route:
            repo = route.split("repos/")[1].split("/releases")[0]
            if repo not in releases:
                return 1, "", "gh: Not Found (HTTP 404)"
            return 0, releases[repo], ""
        repo, path = route.split("repos/")[1].split("/contents/")
        if (repo, path) not in files:
            return 1, "", "gh: Not Found (HTTP 404)"
        import base64
        return 0, base64.b64encode(files[(repo, path)].encode()).decode(), ""
    return run


def test_sweep_judges_a_real_gap_and_excludes_a_sha(monkeypatch):
    monkeypatch.setattr(pin_drift, "latest_k8s", lambda opener=None: ("v1.37.0", None))
    run = _fake_gh(
        files={
            ("o/r", "Dockerfile"): "ARG KUBECTL_VERSION=v1.36.2\n",
            ("o/r", ".github/workflows/build.yaml"):
                "      - uses: actions/checkout@v4\n"
                "      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020\n",
        },
        releases={"actions/checkout": "v7.0.1"},
        trees={"o/r": ["Dockerfile", ".github/workflows/build.yaml", "README.md"]},
    )
    judged, excluded, problems = pin_drift.sweep(["o/r"], run=run)

    assert problems == []
    assert sorted((p["what"], p["gap"]) for p in judged) == [
        ("KUBECTL_VERSION", "minor"),
        ("actions/checkout", "major"),
    ]
    assert [p["what"] for p in excluded] == ["actions/setup-node"]
    assert "hardened form" in excluded[0]["reason"]


def test_sweep_reports_a_repo_with_no_releases_without_calling_it_unreadable(monkeypatch):
    run = _fake_gh(
        files={("o/r", ".github/workflows/build.yaml"):
               "      - uses: someone/never-released@v1\n"},
        releases={},
        trees={"o/r": [".github/workflows/build.yaml"]},
    )
    judged, excluded, problems = pin_drift.sweep(["o/r"], run=run)
    assert judged == []
    assert problems == []
    assert "publishes no releases" in excluded[0]["reason"]


def test_resolve_asks_upstream_once_for_many_files(monkeypatch):
    calls = []

    def run(args):
        calls.append(args[1])
        return 0, "v7.0.1", ""

    cache = {}
    for _ in range(5):
        latest, source, why = pin_drift.resolve(
            {"kind": "action", "what": "actions/checkout"}, cache, run=run)
    assert latest == "v7.0.1" and why is None and "actions/checkout" in source
    assert len(calls) == 1


def test_report_groups_repeated_uses_of_one_pin_in_one_file():
    judged = [
        {"repo": "o/r", "path": ".github/workflows/build.yaml",
         "what": "actions/checkout", "pinned": "v4", "latest": "v7.0.1",
         "gap": "major", "source": "actions/checkout releases"}
        for _ in range(5)
    ]
    text = pin_drift.format_report(judged, [], [], [])
    assert text.count(".github/workflows/build.yaml") == 1
    assert "(5 uses)" in text
    assert "Judged 5 pin(s): 5 behind" in text


def test_a_patch_gap_is_reported_but_does_not_raise(monkeypatch):
    monkeypatch.setattr(pin_drift, "_repos_to_sweep",
                        lambda: (["o/r"], [], [], False))
    monkeypatch.setattr(
        pin_drift, "sweep",
        lambda repos: ([{"repo": "o/r", "path": "Dockerfile", "what": "X_VERSION",
                         "pinned": "1.2.3", "latest": "1.2.9", "gap": "patch",
                         "source": "x releases"}], [], []))
    assert pin_drift.main([]) == 0


def test_drift_outranks_an_incomplete_sweep(monkeypatch):
    # The live bug: one ARG with no configured upstream made `main`
    # return 1, hiding twelve real bumps behind it.
    monkeypatch.setattr(pin_drift, "_repos_to_sweep",
                        lambda: (["o/r"], [], [], False))
    monkeypatch.setattr(
        pin_drift, "sweep",
        lambda repos: ([{"repo": "o/r", "path": "Dockerfile", "what": "X_VERSION",
                         "pinned": "1.2.3", "latest": "2.0.0", "gap": "major",
                         "source": "x releases"}], [],
                       ["o/r: could not read something"]))
    assert pin_drift.main([]) == 2


def test_nothing_judged_is_no_instrument_not_no_drift(monkeypatch):
    monkeypatch.setattr(pin_drift, "_repos_to_sweep",
                        lambda: (["o/r"], [], [], False))
    monkeypatch.setattr(pin_drift, "sweep", lambda repos: ([], [], []))
    assert pin_drift.main([]) == 1
