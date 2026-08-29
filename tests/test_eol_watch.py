"""Tests for `tools.eol_watch` (Cycle 603, idea #151).

The one that carries the row is `test_supported_line_with_no_upstream_gap_is_still_eol`:
`node:20-alpine` is the newest Node 20 that will ever exist, so
`pin_drift` calls it current and this has to call it dead. That is the
whole difference between "up to date" and "still supported".
"""

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


def judged(image, tag, within_days=eol_watch.DEFAULT_WITHIN_DAYS):
    entry = {"repo": "o/r", "path": "Dockerfile", "image": image, "tag": tag}
    mapping = eol_watch.image_map(PRODUCTS)
    where = eol_watch.judge(entry, PRODUCTS, mapping, TODAY, within_days)
    return where, entry


def test_image_map_is_read_off_the_api_not_typed_in():
    # The purl wins, and the alias covers a product that publishes none.
    mapping = eol_watch.image_map(PRODUCTS)
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
    assert list(groups) == [("node", "20")]
    assert eol_watch._places(groups[("node", "20")]) == [
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
