"""Tests for `tools.eol_watch` (Cycle 603, idea #151).

The one that carries the row is `test_supported_line_with_no_upstream_gap_is_still_eol`:
`node:20-alpine` is the newest Node 20 that will ever exist, so
`pin_drift` calls it current and this has to call it dead. That is the
whole difference between "up to date" and "still supported".
"""

import base64
import datetime as dt

import pytest

from tools import eol_watch


TODAY = dt.date(2026, 8, 29)

PRODUCTS = [
    {
        "name": "nodejs",
        "aliases": ["node"],
        "identifiers": [{"type": "purl", "id": "pkg:docker/library/node"}],
        "releases": [
            {"name": "24", "isEol": False, "eolFrom": "2028-04-30"},
            {"name": "20", "isEol": True, "eolFrom": "2026-04-30"},
            {"name": "22", "isEol": False, "eolFrom": "2026-10-31"},
            {"name": "26", "isEol": False, "eolFrom": None},
        ],
    },
    {
        "name": "alpine-linux",
        "aliases": ["alpine"],
        "identifiers": [],
        "releases": [{"name": "3.20", "isEol": True, "eolFrom": "2026-04-01"}],
    },
]


def judged(image, tag, within_days=eol_watch.DEFAULT_WITHIN_DAYS,
           products=PRODUCTS, **extra):
    entry = {"repo": "o/r", "path": "Dockerfile", "image": image, "tag": tag}
    entry.update(extra)
    mapping, ambiguous = eol_watch.image_map(products)
    where = eol_watch.judge(entry, products, mapping, TODAY, within_days,
                            ambiguous)
    return where, entry


def test_image_map_is_read_off_the_api_not_typed_in():
    # The purl wins, and the alias covers a product that publishes none.
    mapping, _ = eol_watch.image_map(PRODUCTS)
    assert mapping["node"] == "nodejs"
    assert mapping["alpine"] == "alpine-linux"
    assert "totally-made-up" not in mapping


def test_supported_line_with_no_upstream_gap_is_still_eol():
    # idea #151's own proof case. There is no newer Node 20, so a
    # version-gap check reports this healthy forever.
    where, entry = judged("node", "20-alpine")
    assert where == "judged"
    assert entry["verdict"] == "eol"
    assert entry["days"] < 0
    assert entry["variant"] == "alpine"


def test_a_line_inside_the_notice_window_raises_before_the_date():
    # Node 22 ends 2026-10-31, 63 days after TODAY: inside 180, outside 30.
    _, entry = judged("node", "22")
    assert entry["verdict"] == "soon"
    _, narrow = judged("node", "22", within_days=30)
    assert narrow["verdict"] == "supported"


def test_a_live_line_is_supported_and_still_prints_its_days():
    _, entry = judged("node", "24-alpine")
    assert entry["verdict"] == "supported"
    assert entry["days"] == (dt.date(2028, 4, 30) - TODAY).days


@pytest.mark.parametrize(
    "image,tag,fragment",
    [
        # A tag naming no version pins no line to look up.
        ("node", "alpine", "names no version"),
        ("node", "latest", "names no version"),
        # No tag at all follows `latest`.
        ("node", None, "no tag"),
        # A product with no release line by that name -- `node:19` -- is
        # not the same as a product that has no support window.
        ("node", "19", "publishes no release line"),
        # A release with no published end-of-life date is unknown, not safe.
        ("node", "26", "no end-of-life date"),
        # An image endoflife.date has never heard of.
        ("gcr.io/distroless/static", "nonroot", "publishes no product"),
    ],
)
def test_unjudgeable_images_say_why_and_never_read_as_supported(image, tag,
                                                                fragment):
    where, entry = judged(image, tag)
    assert where == "not-judged"
    assert fragment in entry["reason"]
    assert "verdict" not in entry


def test_a_stage_name_is_not_an_image():
    text = ("FROM node:24-alpine AS builder\n"
            "RUN npm ci\n"
            "FROM builder AS runner\n")
    found = eol_watch.base_images("o/r", "Dockerfile", text)
    assert [(f["image"], f["tag"]) for f in found] == [("node", "24-alpine")]


def test_a_digest_or_arg_pinned_from_is_not_read_as_a_line():
    text = ("FROM python@sha256:abc123\n"
            "FROM ${BASE_IMAGE}\n"
            "FROM --platform=linux/amd64 python:3.12-slim\n")
    found = eol_watch.base_images("o/r", "Dockerfile", text)
    # The digest form parses as image `python` with no tag, which the
    # judge files as unjudgeable rather than guessing a line; the `$ARG`
    # form matches nothing at all. What must not happen is a version
    # being invented for either.
    assert ("python", "3.12-slim") in [(f["image"], f["tag"]) for f in found]
    assert all(f["tag"] != "sha256" for f in found)


def test_multi_stage_repeats_are_one_finding_with_both_places():
    members = [
        {"repo": "o/r", "path": "Dockerfile", "image": "node", "tag": "20"},
        {"repo": "o/r", "path": "Dockerfile", "image": "node", "tag": "20"},
        {"repo": "o/s", "path": "Dockerfile", "image": "node", "tag": "20"},
    ]
    groups = eol_watch.group(members)
    assert list(groups) == [("image", "node", "20")]
    assert eol_watch._places(groups[("image", "node", "20")]) == [
        "o/r  Dockerfile", "o/s  Dockerfile"]


def test_report_prints_the_days_left_on_a_supported_line_too():
    # The threshold decides the exit status, never what a reader sees.
    _, live = judged("node", "24")
    report = eol_watch.format_report([live], [], [], [], 180)
    assert "SUPPORTED" in report
    assert "day(s) left" in report


def test_an_unreadable_catalogue_is_not_a_clean_run():
    def boom(*_a, **_k):
        raise OSError("no route to host")

    products, why = eol_watch.catalogue(opener=boom)
    assert products is None
    assert "could not reach" in why


def test_sweep_reports_a_repo_it_could_not_list_as_a_problem():
    def run(args):
        return 1, "", "gh: Not Found"

    judged_, not_judged, problems = eol_watch.sweep(
        ["o/r"], PRODUCTS, TODAY, 180, run=run)
    assert (judged_, not_judged) == ([], [])
    assert problems and "could not list" in problems[0]


# --- Findings from my reviewer on runner#506, each with the input it named.

def test_an_exact_version_resolves_to_the_line_it_refines():
    # The reviewer's severest finding. endoflife.date tracks Node by major,
    # so `node:20.11.0-alpine` matched no release name and fell out as NOT
    # JUDGED, which never raises — a run could exit 0 with a dead line in it.
    where, entry = judged("node", "20.11.0-alpine")
    assert where == "judged"
    assert entry["verdict"] == "eol"
    assert entry["version"] == "20.11.0"


def test_a_prefix_match_is_component_wise_not_string_wise():
    products = [{
        "name": "python", "aliases": [], "identifiers": [],
        "releases": [
            {"name": "3.1", "isEol": True, "eolFrom": "2012-04-09"},
            {"name": "3.12", "isEol": False, "eolFrom": "2028-10-31"},
        ],
    }]
    # `3.1` is a string prefix of `3.12.7` and is a different Python.
    _, entry = judged("python", "3.12.7-slim", products=products)
    assert entry["verdict"] == "supported"
    assert entry["eol"] == "2028-10-31"


def test_a_tag_too_coarse_for_any_single_line_is_still_refused():
    # The other direction, unchanged: `node:2` sits above 20, 22, 24, 26.
    where, entry = judged("node", "2")
    assert where == "not-judged"
    assert "publishes no release line" in entry["reason"]


def test_an_image_name_two_products_claim_maps_to_neither():
    products = [
        {"name": "couchbase-server", "aliases": [],
         "identifiers": [{"id": "pkg:docker/library/server"}],
         "releases": [{"name": "7", "isEol": True, "eolFrom": "2020-01-01"}]},
        {"name": "authentik", "aliases": [],
         "identifiers": [{"id": "pkg:docker/goauthentik/server"}],
         "releases": [{"name": "7", "isEol": False, "eolFrom": "2030-01-01"}]},
    ]
    mapping, ambiguous = eol_watch.image_map(products)
    assert "server" not in mapping
    assert ambiguous["server"] == ["authentik", "couchbase-server"]
    where, entry = judged("ghcr.io/sokratesai/server", "7", products=products)
    assert where == "not-judged"
    assert "not decidable" in entry["reason"]
    assert "authentik" in entry["reason"]


def test_a_stronger_source_still_beats_a_weaker_one():
    # A purl claim is not made ambiguous by an unrelated product's alias.
    products = [
        {"name": "nodejs", "aliases": [],
         "identifiers": [{"id": "pkg:docker/library/node"}], "releases": []},
        {"name": "something-else", "aliases": ["node"], "identifiers": [],
         "releases": []},
    ]
    mapping, ambiguous = eol_watch.image_map(products)
    assert mapping["node"] == "nodejs"
    assert "node" not in ambiguous


@pytest.mark.parametrize(
    "extra,fragment",
    [
        ({"digest": True}, "hardened form"),
        ({"templated": True}, "build argument"),
        ({}, "follows `latest`"),
    ],
)
def test_an_untagged_from_says_which_of_the_three_it_is(extra, fragment):
    # All three parse as tag=None. Calling a digest pin "follows latest" is
    # false about the most tightly pinned form there is.
    _, entry = judged("node", None, **extra)
    assert fragment in entry["reason"]


def test_base_images_marks_the_digest_and_arg_forms():
    text = ("FROM python@sha256:abc123\n"
            "FROM node:${NODE_VERSION}\n"
            "FROM python:3.12-slim\n")
    found = eol_watch.base_images("o/r", "Dockerfile", text)
    # Keyed by position, not by image: two of these three are `python`.
    assert (found[0]["image"], found[0]["digest"]) == ("python", True)
    assert (found[1]["image"], found[1]["templated"]) == ("node", True)
    assert (found[2]["image"], found[2]["tag"]) == ("python", "3.12-slim")
    assert found[2]["digest"] is False and found[2]["templated"] is False


def test_a_tagged_image_is_not_dropped_for_matching_a_stage_alias():
    # The fixture has to contain a *tagged* image whose own token equals a
    # stage alias, or the assertion holds whether or not the tag is checked.
    # My first version used `sokratesai/base` and `otherorg/base`, neither of
    # which equals `base`, and the mutation that drops the tag check passed.
    text = ("FROM node:24 AS base\n"
            "FROM base\n"
            "FROM base:2\n")
    found = [(f["image"], f["tag"]) for f in
             eol_watch.base_images("o/r", "Dockerfile", text)]
    assert ("base", "2") in found        # a real image that shares the name
    assert ("base", None) not in found   # that one really is the stage
    assert ("node", "24") in found


def test_a_catalogue_with_no_products_is_a_problem_not_an_empty_sweep():
    class Body:
        def read(self): return b'{"result": null}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    products, why = eol_watch.catalogue(opener=lambda *a, **k: Body())
    assert products is None
    assert "no products" in why


# --- main()'s exit contract, which had no coverage at all.

def _run_main(monkeypatch, judged_out, not_judged_out=(), problems=()):
    monkeypatch.setattr(eol_watch, "catalogue", lambda *a, **k: (PRODUCTS, None))
    monkeypatch.setattr(eol_watch, "sweep",
                        lambda *a, **k: (list(judged_out),
                                         list(not_judged_out), list(problems)))
    return eol_watch.main(["--repo", "o/r"])


def test_main_exits_2_on_a_line_out_of_support(monkeypatch, capsys):
    _, dead = judged("node", "20")
    assert _run_main(monkeypatch, [dead]) == 2
    assert "RUNTIME SUPPORT" in capsys.readouterr().out


def test_main_exits_0_only_when_something_was_judged_and_all_supported(
        monkeypatch, capsys):
    _, live = judged("node", "24")
    assert _run_main(monkeypatch, [live]) == 0
    # Nothing judged is no instrument, not no finding.
    assert _run_main(monkeypatch, []) == 1
    assert "no instrument" in capsys.readouterr().out


def test_main_exits_1_when_something_was_unreadable(monkeypatch):
    _, live = judged("node", "24")
    assert _run_main(monkeypatch, [live], problems=["o/r: could not list"]) == 1


def test_a_finding_outranks_an_incomplete_sweep(monkeypatch):
    # pin_drift's call: both are true, only one is actionable.
    _, dead = judged("node", "20")
    assert _run_main(monkeypatch, [dead], problems=["o/s: could not list"]) == 2


def test_main_exits_1_when_the_catalogue_is_unreadable(monkeypatch, capsys):
    monkeypatch.setattr(eol_watch, "catalogue",
                        lambda *a, **k: (None, "could not reach it"))
    assert eol_watch.main(["--repo", "o/r"]) == 1
    assert "no instrument" in capsys.readouterr().out


# --- Workflow toolchain pins (Cycle 622).
#
# `SokratesAI/operator` pinned Go 1.25 in four places: one `FROM` line and
# three `setup-go` steps. This tool read the first and reported it as the
# whole answer, so a repo could move its Dockerfile onto a supported line
# and keep building and testing on a dead one with nothing saying so.

WORKFLOW = """\
jobs:
  test:
    steps:
      - uses: actions/setup-node@v7
        with:
          node-version: "20"
          cache: true
      - uses: actions/setup-node@v7
        with:
          node-version-file: .nvmrc
      - name: something else entirely
        with:
          api-version: 2
"""


def test_a_workflow_version_pin_is_read_like_a_from_line():
    pins = eol_watch.toolchain_pins("o/r", ".github/workflows/build.yaml",
                                    WORKFLOW)
    assert [(p["image"], p["tag"]) for p in pins] == [("node", "20")]
    assert all(p["kind"] == "toolchain" for p in pins)


def test_a_version_key_this_file_does_not_install_is_not_a_runtime_pin():
    # My reviewer's finding on runner#523, with its own input. The
    # endoflife.date catalogue's short names collide with ordinary
    # workflow keys -- `app` is istio, `vault` is hashicorp-vault,
    # `server` is claimed by two products -- so resolving in the map is
    # not enough on its own. `app-version: "1.28.0"` in a release job
    # resolved to Istio 1.28, which really is past its end of life, and
    # printed a fabricated finding with a real product name and a real
    # date on it. Only a runtime the file itself installs is read.
    text = """\
      - name: Bump version
        env:
          app-version: "1.28.0"
          vault-version: "1.15"
"""
    assert eol_watch.toolchain_pins("o/r", "w", text) == []
    # And the same key does count once the file says it installs it.
    installed = "      - uses: actions/setup-app@v1\n" + text
    assert [p["image"] for p in eol_watch.toolchain_pins("o/r", "w", installed)] \
        == ["app"]


def test_a_third_party_setup_action_counts_the_same_as_githubs():
    text = ("      - uses: ruby/setup-ruby@v1\n"
            "        with:\n"
            "          ruby-version: \"3.1\"\n")
    assert [(p["image"], p["tag"]) for p in eol_watch.toolchain_pins(
        "o/r", "w", text)] == [("ruby", "3.1")]


def test_a_value_that_names_no_version_is_not_read_as_one():
    # `node-version-file: .nvmrc` points at a file this does not read, so
    # there is nothing in *this* file to judge.
    assert "nvmrc" not in str(eol_watch.toolchain_pins("o/r", "w", WORKFLOW))


def test_the_key_must_end_at_version_and_not_merely_contain_it():
    # This value is contrived on purpose. Every real `*-version-file:`
    # value in the wild -- `.nvmrc`, `go.mod`, `.python-version` -- starts
    # with a letter or a dot, so the "must start with a digit" rule
    # already refuses them and a realistic fixture cannot tell that rule
    # apart from the `-version:` anchor. Only a digit-leading value can
    # show which one is doing the work, and the anchor has to be the one:
    # a file name is not a version however it happens to be spelled.
    text = "          node-version-file: 20-lts.txt\n"
    assert eol_watch.toolchain_pins("o/r", "w", text) == []


TREE = ".github/workflows/build.yaml"


def test_sweep_judges_a_dead_toolchain_pin_and_drops_a_key_that_is_not_one():
    def run(args):
        if any("git/trees" in a for a in args):
            return 0, TREE, ""
        return 0, base64.b64encode(WORKFLOW.encode()).decode(), ""

    judged_, not_judged, problems = eol_watch.sweep(
        ["o/r"], PRODUCTS, TODAY, 180, run=run)
    assert problems == []
    # `api-version: 2` names no product, so it is not a runtime pin at all
    # and is dropped rather than printed as an unanswerable question.
    assert not_judged == []
    assert [(i["image"], i["tag"], i["verdict"]) for i in judged_] == [
        ("node", "20", "eol")]
    assert judged_[0]["path"] == ".github/workflows/build.yaml"


def test_the_report_writes_a_workflow_pin_the_way_the_file_writes_it():
    _, pin = judged("node", "20", kind="toolchain")
    report = eol_watch.format_report([pin], [], [], [], 180)
    assert "node-version: 20" in report
    assert "node:20" not in report
    assert "1 workflow version pin(s)" in report


def test_a_from_line_and_a_workflow_pin_of_one_version_stay_apart():
    # Same runtime, same version, two notations in two kinds of file. One
    # group would print one of them under the other's spelling.
    groups = eol_watch.group([
        {"repo": "o/r", "path": "Dockerfile", "image": "node", "tag": "20"},
        {"repo": "o/r", "path": ".github/workflows/b.yaml", "image": "node",
         "tag": "20", "kind": "toolchain"},
    ])
    assert len(groups) == 2
