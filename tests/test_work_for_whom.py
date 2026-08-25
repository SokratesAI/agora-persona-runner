"""`tools.work_for_whom` -- whose surface my merged work touched.

Everything here drives the pure half (`classify_path`, `classify_pr`,
`surface_report`, `board_report`, `render`, `render_rules`) with hand-built
inputs. The two fetches are a `gh` subprocess and one HTTP call and are
exercised by running the tool, not by mocking them into asserting their own
arguments back.

The test that matters most is `test_a_product_change_with_its_test_is_not_both`.
The first version of this tool counted `tests/` as a surface of its own, and
27 of 60 real PRs landed in `both` -- which reads as "half my work serves
both of us" and actually meant "I write tests alongside my code". A
classifier whose largest bucket is an artefact of my own habits is worse
than no classifier, because the number still looks like a measurement.
"""

import pytest

from tools.work_for_whom import (
    HIS,
    MINE,
    board_report,
    classify_path,
    classify_pr,
    render,
    render_rules,
    surface_report,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("agora_runner/nova_site.py", HIS),
        ("agora_runner/nova_public/app.js", HIS),
        ("src/index.ts", HIS),
        ("agora_runner/nova_public/style.css", HIS),
        ("tools/claim.py", MINE),
        ("agora_runner/turns.py", MINE),
        ("agora_runner/poll.py", MINE),
        ("README.md", None),
        ("Dockerfile", None),
        ("tests/test_claim.py", None),
        (".github/workflows/build.yaml", None),
    ],
)
def test_classify_path(path, expected):
    assert classify_path(path) == expected


def test_a_product_change_with_its_test_is_not_both():
    assert classify_pr(["agora_runner/nova_site.py", "tests/test_nova_site.py"]) == HIS


def test_a_tool_change_with_its_test_is_mine():
    assert classify_pr(["tools/claim.py", "tests/test_claim.py"]) == MINE


def test_a_test_only_change_is_mine():
    assert classify_pr(["tests/test_claim.py"]) == MINE


def test_ci_only_change_is_mine():
    assert classify_pr([".github/workflows/build.yaml"]) == MINE


def test_both_needs_two_real_surfaces_not_a_test_file():
    assert classify_pr(["agora_runner/nova_site.py", "tools/claim.py"]) == "both"


def test_a_change_no_rule_claims_is_not_folded_into_either_side():
    assert classify_pr(["README.md", "Dockerfile"]) == "unclassified"


def test_supporting_files_never_outvote_a_real_surface():
    """Ten test files beside one product file still read as the product."""
    paths = ["agora_runner/nova_site.py"] + [f"tests/test_{i}.py" for i in range(10)]
    assert classify_pr(paths) == HIS


def _pr(number, title, paths):
    return {"number": number, "title": title, "files": [{"path": p} for p in paths]}


def test_surface_report_counts_and_labels():
    prs = [
        _pr(3, "app tweak", ["agora_runner/nova_site.py"]),
        _pr(2, "a tool", ["tools/claim.py", "tests/test_claim.py"]),
        _pr(1, "readme", ["README.md"]),
    ]
    counts, labelled = surface_report(prs)
    assert counts == {HIS: 1, MINE: 1, "both": 0, "unclassified": 1}
    assert labelled == [(HIS, 3, "app tweak"), (MINE, 2, "a tool"),
                        ("unclassified", 1, "readme")]


def test_surface_report_survives_a_pr_with_no_file_list():
    """`gh` returns `files: null` on a PR it could not expand."""
    counts, _ = surface_report([{"number": 9, "title": "x", "files": None}])
    assert counts["unclassified"] == 1


def test_board_report_counts_named_rows_shipped_prs_and_outcomes():
    entries = [
        {"board": "idea #68", "pr": "#160", "outcome": "merged"},
        {"board": "", "pr": "#161", "outcome": "merged"},
        {"board": "  ", "pr": "none", "outcome": "no-op"},
        {"board": "issue #71", "pr": "", "outcome": "stuck"},
    ]
    report = board_report(entries)
    assert report["entries"] == 4
    assert report["named_a_board_row"] == 2
    assert report["shipped_a_pr"] == 2
    assert report["outcomes"] == {"merged": 2, "no-op": 1, "stuck": 1}


def test_board_report_treats_a_missing_outcome_as_unstated():
    assert board_report([{}])["outcomes"] == {"unstated": 1}


def test_render_states_its_limits_even_when_everything_worked():
    counts, labelled = surface_report([_pr(1, "t", ["tools/claim.py"])])
    body = render(counts, labelled, board_report([{"board": "idea #1", "pr": "#1",
                                                   "outcome": "merged"}]),
                  "SokratesAI/agora-persona-runner", 60, 60, [])
    assert "WHAT THIS CANNOT SEE" in body
    assert "One repo" in body
    assert "--rules" in body


def test_render_surfaces_a_fetch_failure_rather_than_printing_a_clean_zero():
    counts, labelled = surface_report([])
    body = render(counts, labelled, board_report([]),
                  "SokratesAI/agora-persona-runner", 60, 60, ["gh pr list failed: boom"])
    assert "gh pr list failed: boom" in body
    assert "no merged PRs answered" in body


def test_render_rules_prints_every_rule_and_the_supporting_ones_apart():
    body = render_rules()
    assert "agora_runner/nova_public/" in body
    assert "LOOKED AT ONLY WHEN A PR TOUCHES NOTHING ABOVE" in body
    assert "tests/" in body.split("LOOKED AT ONLY WHEN A PR TOUCHES NOTHING ABOVE")[1]
