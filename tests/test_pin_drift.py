"""Tests for `tools.pin_drift` (Cycle 561, idea #141).

The two that matter most are the ones written after a wrong answer on a
live run, not before it: a commit-SHA pin read as `major 3` and reported
four majors behind, and a drift finding hidden behind exit 1 because one
unrelated pin had no upstream configured.
"""

import contextlib
import io
import json

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
        # Was "current" until Cycle 589 taught KUBECTL_VERSION a ceiling it
        # can be past. Ahead is still not behind -- it is now its own
        # verdict rather than being folded into clean.
        ("2.0.0", "1.9.9", "ahead"),
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


def test_interesting_paths_picks_dockerfiles_workflows_and_crossplane():
    paths = [
        "Dockerfile",
        "offbox/Dockerfile",
        "Dockerfile.test",
        ".github/workflows/build.yaml",
        ".github/workflows/docs.yml",
        ".github/dependabot.yml",
        "crossplane/githubservice-composition.yaml",
        "crossplane/claims/service-agora.yml",
        "crossplane/README.md",
        "README.md",
        "src/Dockerfile.md",
    ]
    assert pin_drift.interesting_paths(paths) == [
        ".github/workflows/build.yaml",
        ".github/workflows/docs.yml",
        "Dockerfile",
        "Dockerfile.test",
        "crossplane/claims/service-agora.yml",
        "crossplane/githubservice-composition.yaml",
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


def test_pins_in_reads_uses_lines_behind_an_escaped_newline():
    """The Crossplane composition writes a whole workflow as one escaped string.

    Every `uses:` in it is preceded by a literal backslash-n rather than a
    real newline, so `USES_RE`'s `^` never reaches one and this tool read
    the file as carrying no pins at all.
    """
    text = (
        'apiVersion: apiextensions.crossplane.io/v1\n'
        'spec:\n'
        '  resources:\n'
        '    - name: source-workflow\n'
        '      base:\n'
        '        spec:\n'
        '          forProvider:\n'
        '            file: .github/workflows/build.yaml\n'
        '            content: "name: build\\njobs:\\n  test:\\n'
        '    steps:\\n      - uses: actions/checkout@v7\\n'
        '      - uses: docker/build-push-action@v7\\n'
        '      - run: echo uses: fake/action@v9\\n"\n'
    )
    pins = pin_drift.pins_in("o/r", "crossplane/githubservice-composition.yaml", text)
    assert [(p["what"], p["pinned"], p["kind"]) for p in pins] == [
        ("actions/checkout", "v7", "template-action"),
        ("docker/build-push-action", "v7", "template-action"),
    ]


def test_pins_in_does_not_report_the_same_pin_twice_from_one_file():
    """A composition may pin an action for itself and again in what it writes."""
    text = (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v7\n"
        '            content: "steps:\\n      - uses: actions/checkout@v7\\n"\n'
    )
    pins = pin_drift.pins_in("o/r", "crossplane/x.yaml", text)
    assert [(p["what"], p["pinned"], p["kind"]) for p in pins] == [
        ("actions/checkout", "v7", "action"),
    ]


def test_pins_in_still_separates_two_different_refs_of_one_action():
    """Deduplication is on (action, ref), never on the action alone."""
    text = (
        "steps:\n"
        "      - uses: actions/checkout@v7\n"
        '  content: "steps:\\n      - uses: actions/checkout@v3\\n"\n'
    )
    pins = pin_drift.pins_in("o/r", "crossplane/x.yaml", text)
    assert [(p["what"], p["pinned"], p["kind"]) for p in pins] == [
        ("actions/checkout", "v7", "action"),
        ("actions/checkout", "v3", "template-action"),
    ]


def test_resolve_reads_a_template_action_upstream_like_any_other_action():
    calls = []

    def run(args):
        calls.append(args)
        return 0, "v7.2.3", ""

    pin = {"what": "goreleaser/goreleaser-action", "pinned": "v5",
           "kind": "template-action"}
    latest, source, why = pin_drift.resolve(pin, {}, run=run)
    assert (latest, why) == ("v7.2.3", None)
    assert source == "goreleaser/goreleaser-action releases"


def test_report_marks_a_template_pin_as_stamped_into_new_repos():
    judged = [
        {"repo": "SokratesAI/platform-config",
         "path": "crossplane/githubservice-composition.yaml",
         "what": "actions/checkout", "pinned": "v3", "latest": "v7",
         "source": "actions/checkout releases", "gap": "major",
         "kind": "template-action"},
        {"repo": "SokratesAI/agora", "path": ".github/workflows/build.yaml",
         "what": "actions/checkout", "pinned": "v3", "latest": "v7",
         "source": "actions/checkout releases", "gap": "major",
         "kind": "action"},
    ]
    report = pin_drift.format_report(judged, [], [], [])
    assert ("      SokratesAI/platform-config  "
            "crossplane/githubservice-composition.yaml"
            "  — a template, stamped into every repo it creates") in report
    assert ("      SokratesAI/agora  .github/workflows/build.yaml\n") in report


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
    monkeypatch.setattr(pin_drift, "latest_k8s",
                        lambda opener=None, run=None: ("v1.37.0", None))
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


# --- KUBECTL_VERSION is judged against the cluster, not against upstream ---
# Cycle 589. The check reported v1.36.2 as "a minor behind v1.37.0" and asked
# for a bump, against an API server at v1.34.4+k3s1 that kubectl already warns
# is two minors away. The ceiling belongs to the cluster.

def _fake_kubectl(major, minor, code=0, err=""):
    def run(args):
        assert args == ["version", "-o", "json"]
        if code != 0:
            return code, "", err
        return 0, json.dumps({
            "serverVersion": {"major": major, "minor": minor,
                              "gitVersion": f"v{major}.{minor}.4+k3s1"},
        }), ""
    return run


def _fake_opener(published):
    def opener(url, timeout=None):
        if url not in published:
            raise OSError("HTTP Error 404: Not Found")
        return contextlib.closing(io.BytesIO(published[url].encode()))
    return opener


def test_latest_k8s_ceiling_is_one_minor_past_the_api_server():
    # The server's own minor is published here on purpose. Without it the
    # order of the two candidates does not matter -- 1.34 404s and 1.35
    # answers either way -- and a mutation swapping them passed.
    latest, why = pin_drift.latest_k8s(
        opener=_fake_opener({
            "https://dl.k8s.io/release/stable-1.34.txt": "v1.34.9",
            "https://dl.k8s.io/release/stable-1.35.txt": "v1.35.8",
            "https://dl.k8s.io/release/stable-1.37.txt": "v1.37.0",
        }),
        run=_fake_kubectl("1", "34"),
    )
    assert (latest, why) == ("v1.35.8", None)


def test_latest_k8s_falls_back_to_the_server_minor_when_the_next_is_unpublished():
    latest, why = pin_drift.latest_k8s(
        opener=_fake_opener({"https://dl.k8s.io/release/stable-1.34.txt": "v1.34.9"}),
        run=_fake_kubectl("1", "34"),
    )
    assert (latest, why) == ("v1.34.9", None)


def test_server_minor_reads_a_plus_suffixed_minor():
    where, git_version, why = pin_drift.server_minor(_fake_kubectl("1", "34+"))
    assert (where, why) == ((1, 34), None)
    assert git_version == "v1.34+.4+k3s1"


def test_latest_k8s_says_it_could_not_read_rather_than_guessing():
    latest, why = pin_drift.latest_k8s(
        opener=_fake_opener({"https://dl.k8s.io/release/stable.txt": "v1.37.0"}),
        run=_fake_kubectl("1", "34", code=1, err="Unable to connect to the server"),
    )
    assert latest is None
    assert "could not read the cluster's API server version" in why
    assert "Unable to connect" in why


def test_gap_calls_a_pin_past_its_ceiling_ahead_not_current():
    assert pin_drift.gap("v1.36.2", "v1.35.8") == "ahead"
    # The floating-tag reading is unchanged: v4 says nothing about a minor.
    assert pin_drift.gap("v4", "v4.9.0") == "current"
    assert pin_drift.gap("v1.35.8", "v1.35.8") == "current"


def test_sweep_and_report_raise_a_kubectl_pin_that_is_ahead(monkeypatch):
    monkeypatch.setattr(pin_drift, "latest_k8s",
                        lambda opener=None, run=None: ("v1.35.8", None))
    run = _fake_gh(
        files={("o/r", "Dockerfile"): "ARG KUBECTL_VERSION=v1.36.2\n"},
        releases={},
        trees={"o/r": ["Dockerfile"]},
    )
    judged, excluded, problems = pin_drift.sweep(["o/r"], run=run)

    assert problems == []
    assert [(p["what"], p["gap"]) for p in judged] == [("KUBECTL_VERSION", "ahead")]

    report = pin_drift.format_report(judged, excluded, problems, [])
    assert "PINNED PAST WHAT IT IS JUDGED AGAINST" in report
    assert "KUBECTL_VERSION: pinned v1.36.2, ceiling v1.35.8" in report
    assert "      o/r  Dockerfile" in report
    assert "3 behind" not in report
    assert "Judged 1 pin(s): 0 behind, 1 ahead, 0 patch-only, 0 current." in report


def test_an_unreadable_cluster_is_a_problem_not_a_clean_answer(monkeypatch):
    monkeypatch.setattr(
        pin_drift, "latest_k8s",
        lambda opener=None, run=None: (None, "could not read the cluster's "
                                             "API server version — nope"),
    )
    run = _fake_gh(
        files={("o/r", "Dockerfile"): "ARG KUBECTL_VERSION=v1.36.2\n"},
        releases={},
        trees={"o/r": ["Dockerfile"]},
    )
    judged, excluded, problems = pin_drift.sweep(["o/r"], run=run)

    assert judged == []
    assert len(problems) == 1
    assert "upstream unreadable" in problems[0]
    assert "could not read the cluster's API server version" in problems[0]
