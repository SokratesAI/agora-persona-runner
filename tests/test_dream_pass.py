"""Tests for the read-only staleness pass over Nova's own capture files.

The property that matters most is not what the tool flags -- it is that
`--proposal` cannot lose a sentence. Idea #83's own note names that as
the design risk ("can quietly delete a fact I needed, and I would not
know, because the evidence would be gone"), so the round-trip tests
below compare every bullet as a multiset. The first version compared
sets; the reviewer on runner#296 built the input that beats that -- two
identical bullets in, one deleted out, no complaint -- and a tool whose
job is finding duplicates cannot verify itself with a comparison that
collapses them.
"""

import os


from tools import dream_pass


HEADER = "# Nova — Issues\n\n"


def doc(*bullets):
    return HEADER + "\n".join(bullets) + "\n"


def test_parse_takes_top_level_bullets_and_their_continuations():
    text = doc(
        "- 2026-08-01 (Cycle 1) — first note",
        "  continued on a second line",
        "- 2026-08-02 (Cycle 2) — second note",
    )
    bullets = dream_pass.parse(text)
    assert len(bullets) == 2
    assert "continued on a second line" in bullets[0].text
    assert bullets[1].cycle == 2
    assert bullets[1].date == "2026-08-02"


def test_parse_ignores_headings_and_prose():
    text = "# Nova — Issues\n\nCrude capture only.\n\n- a real bullet\n"
    assert len(dream_pass.parse(text)) == 1


def test_done_marker_is_read_from_the_head_line_only():
    bullets = dream_pass.parse(doc(
        "- DONE (Cycle 9): fixed in runner#1 — 2026-08-01 (Cycle 5) — the thing",
        "- 2026-08-02 (Cycle 6) — a note that mentions DONE in passing",
    ))
    assert [b.done for b in bullets] == [True, False]


def test_done_marker_is_seen_through_the_date_prefix_the_loop_actually_writes():
    """The first two shapes are the live file; the third is tolerance.

    `prompt.md` step 6 tells every cycle to open a bullet with the date and
    its own cycle number, and to close one by making it "start with
    `DONE (Cycle N):`". Both are followed, so a closed bullet carries the
    marker *after* the prefix, and the original `^- DONE` pattern saw none
    of the three in `resources/issues.md` on 2026-08-26 (Cycles 197, 228,
    312). The old prefix-less shape is still in the file 14 times and must
    keep matching. The bolded third shape is not in the file today -- a
    bold lead is the house style for a bullet's first clause, so a cycle
    will write it eventually, and the pattern allows it on purpose.
    """
    bullets = dream_pass.parse(doc(
        "- DONE (Cycle 9): the old shape, still in the Retired section",
        "- 2026-08-22 (Cycle 312) — DONE (Cycle 312): the heartbeat named no shell",
        "- 2026-08-16 (Cycle 228) — **DONE (Cycle 228)**: the four CI-blocked PRs are drafts",
    ))
    assert [b.done for b in bullets] == [True, True, True]


def test_a_bullet_describing_the_done_convention_is_not_retired():
    """The five decoys are why the fix is not "contains DONE (Cycle".

    Every one of these is a live note *about* the marker, sitting in the
    same section as the real ones. Retiring a bullet that documents the
    mechanism would be the tool deleting the description of itself.
    """
    bullets = dream_pass.parse(doc(
        "- 2026-08-20 (Cycle 285) — reads a capture as unprocessed unless the"
        " bullet starts exactly `DONE (Cycle N):` and his did not",
        "- 2026-08-17 (Cycle 251) — a capture marked `DONE (Cycle N):` is never"
        " removed from his file",
        "- 2026-08-14 (Cycle 183) — not one bullet is marked done, because"
        " nothing has ever written DONE (Cycle N) here",
    ))
    assert [b.done for b in bullets] == [False, False, False]


def test_propose_moves_done_bullets_and_keeps_every_other_word(tmp_path):
    before = doc(
        "- DONE (Cycle 9): fixed — 2026-08-01 (Cycle 5) — the first thing",
        "- 2026-08-02 (Cycle 6) — a live note",
        "- DONE (Cycle 10): also fixed — 2026-08-03 (Cycle 7) — the second thing",
    )
    after, moved = dream_pass.propose(before)

    assert len(moved) == 2
    assert dream_pass.check(before, after, moved) is None
    assert "## Retired" in after
    # Order within the file changed; the set of bullets did not.
    assert {b.text for b in dream_pass.parse(before)} == \
           {b.text for b in dream_pass.parse(after)}
    # The live note stays above the retired section.
    live, retired = after.split("## Retired", 1)
    assert "a live note" in live
    assert "a live note" not in retired
    assert "the second thing" in retired


def test_propose_is_a_no_op_when_nothing_is_done():
    before = doc("- 2026-08-02 (Cycle 6) — a live note")
    after, moved = dream_pass.propose(before)
    assert moved == []
    assert after == before


def test_propose_is_idempotent():
    before = doc(
        "- DONE (Cycle 9): fixed — the first thing",
        "- 2026-08-02 (Cycle 6) — a live note",
    )
    once, _ = dream_pass.propose(before)
    twice, moved = dream_pass.propose(once)
    assert moved == []
    assert twice == once


def test_check_catches_a_bullet_that_went_missing():
    before = doc("- DONE (Cycle 9): fixed — the first thing",
                 "- 2026-08-02 (Cycle 6) — a live note")
    after, moved = dream_pass.propose(before)
    mangled = after.replace("- 2026-08-02 (Cycle 6) — a live note\n", "")
    assert "vanished" in dream_pass.check(before, mangled, moved)


def test_paths_only_come_from_backticked_file_shaped_spans():
    bullets = dream_pass.parse(doc(
        "- a note about `tools/thing.py` and `agora_runner/other.js`",
        "- a note about the poll loop and his ideas.md and `cli.py`",
        "- a note about `projects/sokrates/projects/nova/issues.md`",
    ))
    assert bullets[0].paths() == ["tools/thing.py", "agora_runner/other.js"]
    # No directory, and no backticks: neither is checkable.
    assert bullets[1].paths() == []
    # A vault document is not in any checkout and never could be.
    assert bullets[2].paths() == []


def test_dead_paths_needs_a_checkout_to_answer(tmp_path):
    os.makedirs(tmp_path / "tools")
    bullets = dream_pass.parse(doc("- a note about `tools/gone.py`"))
    assert dead_names(dream_pass.dead_paths(bullets, [])) == []
    assert dead_names(dream_pass.dead_paths(bullets, [str(tmp_path)])) == \
        [["tools/gone.py"]]


def dead_names(flagged):
    return [missing for _bullet, missing, _unknown in flagged if missing]


def unknown_names(flagged):
    return [unknown for _bullet, _missing, unknown in flagged if unknown]


def test_a_live_path_is_not_flagged(tmp_path):
    os.makedirs(tmp_path / "tools")
    (tmp_path / "tools" / "here.py").write_text("")
    bullets = dream_pass.parse(doc("- a note about `tools/here.py`"))
    assert dream_pass.dead_paths(bullets, [str(tmp_path)]) == []


def test_a_path_prefixed_with_its_own_repo_name_resolves(tmp_path):
    repo = tmp_path / "platform-config"
    os.makedirs(repo / "deployments")
    (repo / "deployments" / "app.yaml").write_text("")
    bullets = dream_pass.parse(doc(
        "- a note about `platform-config/deployments/app.yaml`"))
    assert dream_pass.dead_paths(bullets, [str(repo)]) == []


def test_the_repo_name_strip_does_not_match_a_different_repo(tmp_path):
    """A citation into `agora/` is unanswerable from a `platform-config`
    checkout — not dead. It would read as missing whether or not the file
    exists, which is the guaranteed positive this split exists to stop."""
    repo = tmp_path / "platform-config"
    os.makedirs(repo / "deployments")
    (repo / "deployments" / "app.yaml").write_text("")
    bullets = dream_pass.parse(doc(
        "- a note about `agora/deployments/app.yaml`"))
    flagged = dream_pass.dead_paths(bullets, [str(repo)])
    assert dead_names(flagged) == []
    assert unknown_names(flagged) == [["agora/deployments/app.yaml"]]


def test_a_path_whose_top_directory_is_absent_is_unanswerable(tmp_path):
    os.makedirs(tmp_path / "tools")
    bullets = dream_pass.parse(doc(
        "- a note about `deployments/agents/newspaper/configmaps.yaml`"))
    flagged = dream_pass.dead_paths(bullets, [str(tmp_path)])
    assert dead_names(flagged) == []
    assert unknown_names(flagged) == \
        [["deployments/agents/newspaper/configmaps.yaml"]]


def test_one_bullet_can_carry_both_kinds(tmp_path):
    os.makedirs(tmp_path / "tools")
    bullets = dream_pass.parse(doc(
        "- cites `tools/gone.py` and `agora/public/app.js`"))
    flagged = dream_pass.dead_paths(bullets, [str(tmp_path)])
    assert dead_names(flagged) == [["tools/gone.py"]]
    assert unknown_names(flagged) == [["agora/public/app.js"]]


def test_a_repo_prefixed_path_is_answerable_even_when_its_directory_is_gone(tmp_path):
    """The checkout is *named* `platform-config`, so a citation starting
    `platform-config/` is unambiguously about it — dead, not unanswerable,
    even though `deployments/` is exactly what was renamed away. Reviewer
    on runner#364: this is the primary rot case, not an edge one."""
    repo = tmp_path / "platform-config"
    os.makedirs(repo)
    bullets = dream_pass.parse(doc(
        "- a note about `platform-config/deployments/app.yaml`"))
    flagged = dream_pass.dead_paths(bullets, [str(repo)])
    assert dead_names(flagged) == [["platform-config/deployments/app.yaml"]]
    assert unknown_names(flagged) == []


def test_a_repo_prefixed_bare_filename_is_answerable(tmp_path):
    """`tools/gone.py` against a checkout literally named `tools` has no
    second directory segment to test, and is still answerable."""
    repo = tmp_path / "tools"
    os.makedirs(repo)
    bullets = dream_pass.parse(doc("- a note about `tools/gone.py`"))
    flagged = dream_pass.dead_paths(bullets, [str(repo)])
    assert dead_names(flagged) == [["tools/gone.py"]]
    assert unknown_names(flagged) == []


def test_a_missing_file_under_a_directory_i_have_is_still_dead(tmp_path):
    """The check is on the *top* directory, deliberately: seeing `tools/`
    is enough to say this checkout has no `tools/sub/gone.py`."""
    os.makedirs(tmp_path / "tools")
    bullets = dream_pass.parse(doc("- a note about `tools/sub/gone.py`"))
    assert dead_names(dream_pass.dead_paths(bullets, [str(tmp_path)])) == \
        [["tools/sub/gone.py"]]


def test_paths_mean_defaults_off_the_filename():
    assert dream_pass.paths_mean_for("/tmp/my-ideas.md") == "unbuilt"
    assert dream_pass.paths_mean_for("/tmp/issues.md") == "rot"
    assert dream_pass.paths_mean_for("/tmp/notes.md") == "rot"


def test_the_report_says_which_reading_it_used(tmp_path):
    os.makedirs(tmp_path / "tools")
    bullets = dream_pass.parse(doc("- a note about `tools/gone.py`"))
    rot = dream_pass.report(bullets, [str(tmp_path)], 0.5, "rot")
    unbuilt = dream_pass.report(bullets, [str(tmp_path)], 0.5, "unbuilt")
    assert "renamed or deleted" in rot
    assert "not been built yet" in unbuilt
    # Same evidence either way — only the sentence changes.
    assert "tools/gone.py" in rot and "tools/gone.py" in unbuilt


def test_the_report_keeps_the_two_headings_apart(tmp_path):
    os.makedirs(tmp_path / "tools")
    bullets = dream_pass.parse(doc(
        "- cites `tools/gone.py` and `agora/public/app.js`"))
    body = dream_pass.report(bullets, [str(tmp_path)], 0.5)
    dead = body.index("DEAD PATH")
    cannot = body.index("CANNOT CHECK")
    assert dead < cannot
    # The excerpt under each flag quotes the whole bullet, so assert on the
    # line that lists the flagged paths — that is what claims anything.
    def listed(section):
        return [l.split(None, 1)[1] for l in section.split("\n")
                if l.startswith("  L")]
    assert listed(body[dead:cannot]) == ["tools/gone.py"]
    assert listed(body[cannot:body.index("DUPLICATE")]) == ["agora/public/app.js"]


def test_duplicates_cluster_two_reports_of_the_same_bug():
    bullets = dream_pass.parse(doc(
        "- 2026-08-16 (Cycle 227) — `vault_tool.py recent 12` returned 31 rows;"
        " the count argument does not bound the result",
        "- 2026-08-13 (Cycle 170) — `vault_tool.py recent 12` returned 35 rows;"
        " the count argument does not bound the result",
        "- 2026-08-14 (Cycle 200) — the newspaper generator walks categories in"
        " config order and dies in the same place",
    ))
    clusters = dream_pass.duplicates(bullets, 0.5)
    assert len(clusters) == 1
    assert {b.index for b in clusters[0]} == {0, 1}


def test_duplicates_do_not_fire_on_shared_boilerplate():
    """The stopword list is load-bearing, so break it here and not by eye.

    These two bullets are about different things and share almost every
    grammatical word. Without `_STOP` their overlap is 0.71 and the
    clusterer joins them; with it the overlap is 0.43 and it does not.
    The first version of this test used two ordinary sentences and
    passed with the stopword list deleted -- it pinned nothing, and the
    mutation check is what said so.
    """
    bullets = dream_pass.parse(doc(
        "- it is not the case that this is a bug in the form and it does not"
        " have a test at all",
        "- it is not the case that this is a bug in the cache and it does not"
        " have a log at all",
    ))
    assert dream_pass.duplicates(bullets, 0.5) == []


def test_main_refuses_to_write_the_proposal_over_the_source(tmp_path, capsys):
    path = tmp_path / "issues.md"
    path.write_text(doc("- DONE (Cycle 9): fixed — a thing"))
    rc = dream_pass.main(["--file", str(path), "--proposal", str(path)])
    assert rc == 1
    assert "never edits in place" in capsys.readouterr().err
    # And the source really is untouched.
    assert "## Retired" not in path.read_text()


def test_main_refuses_a_checkout_that_is_not_there(tmp_path, capsys):
    path = tmp_path / "issues.md"
    path.write_text(doc("- a note"))
    assert dream_pass.main(["--file", str(path), "--repo", str(tmp_path / "nope")]) == 1
    assert "no such checkout" in capsys.readouterr().err


def test_main_reports_and_writes_a_proposal(tmp_path, capsys):
    src = tmp_path / "issues.md"
    out = tmp_path / "proposed.md"
    src.write_text(doc(
        "- DONE (Cycle 9): fixed — a finished thing",
        "- 2026-08-02 (Cycle 6) — a live note about `tools/gone.py`",
    ))
    rc = dream_pass.main(["--file", str(src), "--repo", str(tmp_path),
                          "--proposal", str(out)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "2 bullets, 1 marked DONE" in printed
    assert "tools/gone.py" in printed
    assert src.read_text() == doc(
        "- DONE (Cycle 9): fixed — a finished thing",
        "- 2026-08-02 (Cycle 6) — a live note about `tools/gone.py`",
    )
    assert "## Retired" in out.read_text()


def test_duplicate_threshold_extremes_behave_as_stated():
    """0.0 joins everything and 1.0 joins only identical word sets.

    The first version of this only asserted `duplicates()` did not
    raise, which the reviewer correctly called a test that pins
    nothing: clustering could break at both extremes and it would pass.
    """
    bullets = dream_pass.parse(doc("- one note", "- another entirely",
                                   "- one note"))
    everything = dream_pass.duplicates(bullets, 0.0)
    assert len(everything) == 1 and len(everything[0]) == 3
    identical = dream_pass.duplicates(bullets, 1.0)
    assert len(identical) == 1
    assert {b.index for b in identical[0]} == {0, 2}


def test_check_notices_a_deleted_copy_of_a_duplicated_bullet():
    """The reviewer's input, and the reason `check` counts rather than sets.

    Two byte-identical bullets go in, one comes out deleted, and a
    set-based comparison calls that fine because the text still exists
    somewhere. A tool built to find duplicates cannot verify itself
    with a comparison that collapses them.
    """
    before = doc(
        "- DONE (Cycle 9): fixed — the thing",
        "- DONE (Cycle 9): fixed — the thing",
        "- 2026-08-02 (Cycle 6) — a live note",
    )
    after, moved = dream_pass.propose(before)
    assert len(moved) == 2
    assert dream_pass.check(before, after, moved) is None

    mangled = after.replace("- DONE (Cycle 9): fixed — the thing\n", "", 1)
    assert "vanished" in dream_pass.check(before, mangled, moved)


def test_done_needs_the_cycle_number_not_just_the_word():
    bullets = dream_pass.parse(doc(
        "- DONE (Cycle 9): fixed — a real marker",
        "- DONE (Cycle 221, runner#212): built — the other shape in the file",
        "- DONE deal, moving on to the next thing",
    ))
    assert [b.done for b in bullets] == [True, True, False]


def test_propose_appends_under_a_retired_section_that_already_has_rows():
    before = doc(
        "- DONE (Cycle 9): the new one",
        "- 2026-08-02 (Cycle 6) — a live note",
    ) + "\n## Retired\n\n- DONE (Cycle 1): an older one\n"
    after, moved = dream_pass.propose(before)
    assert len(moved) == 1
    assert dream_pass.check(before, after, moved) is None
    retired = after.split("## Retired", 1)[1]
    assert "an older one" in retired and "the new one" in retired
    assert after.count("## Retired") == 1
    # And it still settles: a second run has nothing left above the cutoff.
    assert dream_pass.propose(after) == (after, [])


def test_propose_moves_a_done_bullets_continuation_lines_with_it():
    before = doc(
        "- DONE (Cycle 9): fixed — the thing",
        "  and here is the second line of that same bullet",
        "- 2026-08-02 (Cycle 6) — a live note",
    )
    after, moved = dream_pass.propose(before)
    assert dream_pass.check(before, after, moved) is None
    live, retired = after.split("## Retired", 1)
    assert "second line of that same bullet" in retired
    assert "second line of that same bullet" not in live


def test_main_exits_cleanly_when_the_proposal_path_is_unwritable(tmp_path, capsys):
    src = tmp_path / "issues.md"
    src.write_text(doc("- DONE (Cycle 9): fixed — a thing"))
    rc = dream_pass.main(["--file", str(src),
                          "--proposal", str(tmp_path / "nope" / "out.md")])
    assert rc == 1
    assert "cannot write" in capsys.readouterr().err


def test_main_exits_cleanly_on_a_file_that_is_not_utf8(tmp_path, capsys):
    src = tmp_path / "issues.md"
    src.write_bytes(b"- a note \xff\xfe and then some\n")
    assert dream_pass.main(["--file", str(src)]) == 1
    assert "cannot read" in capsys.readouterr().err


# --- the constitution corpus (--mode constitution) -------------------------
#
# The test that earns its place is `..._under_a_different_anchor`. Running
# this by hand first, with one anchor, called `resources/architecture.md`
# dead; it lives one level up at `projects/sokrates/projects/agora/
# resources/architecture.md`, and reporting that as rot would have put a
# false correction in front of a cycle about to edit its own constitution.

NOVA = "projects/sokrates/projects/agora/nova/"
AGORA = "projects/sokrates/projects/agora/"


def listing(*paths):
    return set(paths)


def test_a_citation_resolves_under_a_different_anchor_than_the_obvious_one():
    # `identity.md` writes this as `resources/architecture.md` and means
    # agora/resources/, not nova/resources/.
    found = dream_pass.resolve_doc(
        "resources/architecture.md", listing(AGORA + "resources/architecture.md"))
    assert found == AGORA + "resources/architecture.md"


def test_a_citation_no_anchor_holds_is_dead():
    assert dream_pass.resolve_doc(
        "context/_idea-template.md", listing(NOVA + "resources/ideas.md")) is None


def test_an_elided_citation_resolves_on_its_tail():
    found = dream_pass.resolve_doc(
        ".../nova/resources/inbox.md", listing(NOVA + "resources/inbox.md"))
    assert found == NOVA + "resources/inbox.md"


def test_a_filename_format_is_not_treated_as_a_citation():
    # `<seq>-cycle-<n>.md` and `NNN-report-<first>-<last>.md` describe a
    # naming convention. Resolving one is meaningless and calling it dead
    # is a positive guaranteed before the check runs.
    refs = dream_pass.cited_docs(
        "put it as `<seq>-cycle-<n>.md`, and the report as "
        "`NNN-report-<first>-<last>.md`, beside `inbox.md`")
    assert refs == ["inbox.md"]


def test_a_citation_repeated_eleven_times_is_dead_once():
    assert dream_pass.cited_docs("`kanban.md` " * 11) == ["kanban.md"]


def test_no_vault_listing_reports_cannot_check_rather_than_all_dead():
    out = dream_pass.constitution_report("identity.md", "`kanban.md`", None, [])
    assert "CANNOT CHECK documents" in out
    assert "DEAD DOCUMENT" not in out


def test_no_checkout_reports_cannot_check_rather_than_every_module_dead():
    out = dream_pass.constitution_report("prompt.md", "`tools.nope`", listing(), [])
    assert "CANNOT CHECK modules" in out
    assert "DEAD MODULE" not in out


def test_a_tools_module_that_exists_in_a_checkout_is_not_flagged(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "lint_entry.py").write_text("")
    _, dead = dream_pass.dead_refs("`tools.lint_entry` and `tools.gone`",
                                   listing(), [str(tmp_path)])
    assert dead == ["gone"]


def test_constitution_mode_reads_every_file_it_is_given(tmp_path, capsys):
    a = tmp_path / "identity.md"
    a.write_text("read `kanban.md`")
    b = tmp_path / "personality.md"
    b.write_text("read `inbox.md`")
    lst = tmp_path / "listing.txt"
    lst.write_text(NOVA + "resources/inbox.md\n")
    rc = dream_pass.main(["--mode", "constitution", "--file", str(a),
                          "--file", str(b), "--vault-listing", str(lst)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "identity.md" in out and "personality.md" in out
    assert "kanban.md" in out
    # The live one must not be reported as dead anywhere in the output.
    assert "    inbox.md" not in out


def test_constitution_mode_exits_1_on_an_empty_vault_listing(tmp_path, capsys):
    # An empty listing and a healthy vault look identical to `resolve_doc`
    # and mean opposite things, so this may not read as "everything dead".
    a = tmp_path / "identity.md"
    a.write_text("read `inbox.md`")
    lst = tmp_path / "listing.txt"
    lst.write_text("\n\n")
    assert dream_pass.main(["--mode", "constitution", "--file", str(a),
                            "--vault-listing", str(lst)]) == 1
    assert "empty vault listing" in capsys.readouterr().err


def test_constitution_mode_refuses_proposal(tmp_path, capsys):
    a = tmp_path / "identity.md"
    a.write_text("read `inbox.md`")
    assert dream_pass.main(["--mode", "constitution", "--file", str(a),
                            "--proposal", str(tmp_path / "out.md")]) == 1
    assert "no meaning in --mode constitution" in capsys.readouterr().err


def test_captures_mode_refuses_more_than_one_file(tmp_path, capsys):
    a = tmp_path / "issues.md"
    a.write_text(doc("- a note"))
    b = tmp_path / "ideas.md"
    b.write_text(doc("- another"))
    assert dream_pass.main(["--file", str(a), "--file", str(b)]) == 1
    assert "reads one --file" in capsys.readouterr().err


def test_dead_refs_with_no_listing_answers_nothing_rather_than_everything():
    # Found by mutation: `constitution_report` prints CANNOT CHECK on its
    # own, so a `dead_refs` that treated a missing listing as an empty one
    # passed every test above while calling every citation in the file
    # dead. That is the guaranteed-positive failure this module already
    # had to be taught once, one function further in.
    docs, mods = dream_pass.dead_refs("`kanban.md` `tools.gone`", None, [])
    assert (docs, mods) == ([], [])


def test_an_elided_citation_survives_extraction_and_then_resolves():
    # End to end through both functions. The tail branch in `resolve_doc`
    # was unreachable from `cited_docs` while `lstrip("./")` was stripping
    # the `...` as a character set, and the unit test above could not see
    # it because it fed `resolve_doc` a string extraction never emitted.
    refs = dream_pass.cited_docs("read `.../nova/resources/inbox.md` first")
    assert refs == [".../nova/resources/inbox.md"]
    assert dream_pass.resolve_doc(refs[0], listing(NOVA + "resources/inbox.md"))


def test_a_dot_slash_prefix_is_still_stripped():
    assert dream_pass.cited_docs("see `./inbox.md`") == ["inbox.md"]
