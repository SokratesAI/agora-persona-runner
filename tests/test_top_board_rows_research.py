"""Does the picker put an existing research write-up in front of the cycle?

Issue #236: `pm-kpi-research-reused` read 6 of 81 against a floor of 50%, and
the diagnosis on the row is that nothing shows a cycle what has already been
investigated at the moment it picks a row. These pin the block that does.
"""

import pytest

from agora_runner.nova_boards import PRIORITY_LABELS, STATUS_LABELS
from agora_runner.nova_boards import parse_board
from agora_runner.nova_next import open_rows_from_contents
from tools import top_board_rows


@pytest.fixture(autouse=True)
def _no_live_vault_reads(monkeypatch):
    """No test here reaches the vault. Same reason as the fixtures in
    `test_top_board_rows.py`: on this box those are real reads inside a unit
    test, and in CI they are a subprocess that does not exist."""
    monkeypatch.setattr(top_board_rows, "fetch_projects", lambda: ("", True))
    monkeypatch.setattr(top_board_rows, "fetch_diagnoses", lambda: ("", True))
    monkeypatch.setattr(top_board_rows, "research_write_ups",
                        lambda *a, **k: ([], None))


SLUGS = [
    "backup-node-2026-08",
    "hetzner-node-2026-08-29",
    "nova-framework-spike-2026-09-17",
    "couchdb-vs-a-real-ticket-store-2026-09-02",
    "gh-aw-groq-2026-08",
    "docs-sync-quota-2026-09-13",
    "yoyo-evolve",
]


def row(title, milestone="", project="", number=1, board="issue"):
    """One parsed board row, built the way the board itself writes one."""
    text = ("## Board\n\n| # | Item | Status | Updated | Priority |\n"
            "|---|---|---|---|---|\n"
            f"| [[#{number} — {title}\\|{number}]] | {title} "
            f"| {STATUS_LABELS['backlog']} | 09-15 "
            f"| {PRIORITY_LABELS['high']} |\n")
    parsed = open_rows_from_contents(parse_board(text), board)[0]
    parsed["milestone"] = milestone
    parsed["project"] = project
    return parsed


def slugs_of(matches):
    return [slug for slug, _ in matches]


def test_a_row_gets_the_write_up_that_shares_two_words_with_it():
    hits = top_board_rows.research_matches(
        row("A backup node at home for when Hetzner is down"), SLUGS)
    assert "backup-node-2026-08" in slugs_of(hits)
    assert "hetzner-node-2026-08-29" in slugs_of(hits)


def test_the_words_that_matched_are_printed_so_a_false_match_is_visible():
    hits = dict(top_board_rows.research_matches(
        row("A backup node at home for when Hetzner is down"), SLUGS))
    assert hits["hetzner-node-2026-08-29"] == ["hetzner", "node"]


def test_a_write_up_sharing_nothing_with_the_row_is_not_offered():
    hits = top_board_rows.research_matches(
        row("A backup node at home for when Hetzner is down"), SLUGS)
    assert "yoyo-evolve" not in slugs_of(hits)
    assert "gh-aw-groq-2026-08" not in slugs_of(hits)


def test_the_date_on_a_slug_is_not_a_word_it_can_match_on():
    """`nas-k3s-2026-08-29` is about NAS and k3s, not about 2026.

    Both halves matter and the second one is why this is not vacuous: a row
    that shares only the date with a write-up gets nothing, and a row that
    shares the topic still gets it out of the same slug."""
    assert top_board_rows.research_matches(
        row("Something that happened in 2026 on the 29th"),
        ["nas-k3s-2026-08-29"]) == []
    assert slugs_of(top_board_rows.research_matches(
        row("Put k3s on the NAS"), ["nas-k3s-2026-08-29"])) \
        == ["nas-k3s-2026-08-29"]


def test_a_plural_on_the_board_meets_a_singular_in_the_slug():
    hits = top_board_rows.research_matches(
        row("The docs sync quotas keep running out"), ["docs-sync-quota-2026-09-13"])
    assert slugs_of(hits) == ["docs-sync-quota-2026-09-13"]


def test_one_shared_word_that_is_ordinary_on_the_board_is_not_a_match():
    """The failure this matcher had first.

    `real` appears in exactly one write-up, so rarity measured in the folder
    alone promotes `couchdb-vs-a-real-ticket-store` onto any row with the word
    `real` in it. It has to be rare on the board too.
    """
    others = [row(f"Make the {word} thing real", number=n)
              for n, word in enumerate(["first", "second", "third", "fourth"], 2)]
    subject = row("Rebuild the frontend on a real, modern, stable framework")
    hits = top_board_rows.research_matches(subject, SLUGS, others)
    assert "couchdb-vs-a-real-ticket-store-2026-09-02" not in slugs_of(hits)
    assert "nova-framework-spike-2026-09-17" in slugs_of(hits)


def test_the_row_itself_does_not_count_against_its_own_words():
    """Passing the whole ranking in, the top row is in it. If it counted as
    another row naming `framework`, every match would be one step rarer than
    it is, which is the sort of off-by-one that only shows up as a missing
    line."""
    subject = row("Rebuild the frontend on a real, modern, stable framework")
    with_self = top_board_rows.research_matches(subject, SLUGS, [subject])
    without = top_board_rows.research_matches(subject, SLUGS, [])
    assert slugs_of(with_self) == slugs_of(without)


def test_the_milestone_and_project_name_the_topic_too():
    """A row titled `Do the thing` under the NAS project is about the NAS."""
    hits = top_board_rows.research_matches(
        row("Finish this", milestone="Get onto the NAS", project="NAS"),
        ["nas-k3s-2026-08-29", "yoyo-evolve"])
    assert slugs_of(hits) == ["nas-k3s-2026-08-29"]


def test_the_block_names_the_folder_path_a_cycle_can_fetch():
    lines = top_board_rows._research_block(
        row("A backup node at home for when Hetzner is down"), SLUGS, None)
    assert any("resources/research/backup-node-2026-08.md" in ln for ln in lines)


def test_no_match_prints_no_block_rather_than_an_empty_heading():
    assert top_board_rows._research_block(row("Something else entirely"),
                                          ["yoyo-evolve"], None) == []


def test_an_unreadable_research_folder_is_said_out_loud():
    """A folder that could not be read and a folder with no match in it look
    identical on the page unless one of them says so."""
    lines = top_board_rows._research_block(row("Anything"), (), "ls exited 1")
    assert len(lines) == 1
    assert "could not read the research folder" in lines[0]
    assert "ls exited 1" in lines[0]


def test_render_puts_the_write_ups_under_the_top_row_and_above_the_next_ones():
    rows = [row("A backup node at home for when Hetzner is down", number=1),
            row("Something else entirely", number=2)]
    out = top_board_rows.render(rows, research_slugs=SLUGS)
    top = out.index("issue #1")
    block = out.index("resources/research/backup-node-2026-08.md")
    nxt = out.index("  next:")
    assert top < block < nxt


def test_render_without_slugs_prints_no_research_block():
    out = top_board_rows.render([row("A backup node at home", number=1)])
    assert "already written down" not in out


def test_render_passes_the_read_failure_through():
    out = top_board_rows.render([row("A backup node", number=1)],
                                research_error="no vault client here")
    assert "could not read the research folder" in out
    assert "no vault client here" in out


def test_main_can_be_told_not_to_read_the_research_folder(tmp_path, capsys,
                                                          monkeypatch):
    """`--no-research` is the flag a run without a vault client uses. It must
    actually skip the listing, not swallow its error."""
    called = []
    monkeypatch.setattr(top_board_rows, "research_write_ups",
                        lambda *a, **k: called.append(1) or ([], None))
    for name in ("issues", "ideas", "notes", "proposed-projects",
                 "projects", "milestones", "seats"):
        (tmp_path / f"{name}.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "claims.json").write_text('{"claims": []}', encoding="utf-8")
    (tmp_path / "diagnoses.md").write_text("{}", encoding="utf-8")
    top_board_rows.main([
        "--issues", str(tmp_path / "issues.md"),
        "--ideas", str(tmp_path / "ideas.md"),
        "--notes", str(tmp_path / "notes.md"),
        "--proposed-projects", str(tmp_path / "proposed-projects.md"),
        "--claims", str(tmp_path / "claims.json"),
        "--projects", str(tmp_path / "projects.md"),
        "--milestone-pins", str(tmp_path / "milestones.md"),
        "--milestone-seats", str(tmp_path / "seats.md"),
        "--diagnoses", str(tmp_path / "diagnoses.md"),
        "--no-research",
    ])
    capsys.readouterr()
    assert called == []
