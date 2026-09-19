import pytest

from tools import llm_wiki as w


def test_parse_outline_reads_pages_with_index_first():
    out = ("Here is the plan:\nPAGE: costs.md | Costs | What things cost.\n"
           "PAGE: index.md | Overview | The topic in short.\n")
    assert w.parse_outline(out) == [("index.md", "Overview", "The topic in short."),
                                    ("costs.md", "Costs", "What things cost.")]


@pytest.mark.parametrize("out, why", [
    ("just prose, no pages", "no 'PAGE"),
    ("PAGE: a.md | A | a\n", "no index.md"),
    ("PAGE: index.md | I | i\nPAGE: index.md | J | j\n", "twice"),
])
def test_parse_outline_refuses_a_partial_wiki(out, why):
    with pytest.raises(w.WikiError, match=why):
        w.parse_outline(out)


def test_page_name_outside_the_pattern_is_not_a_page():
    # A path in the name would write outside wiki/; it must not match at all.
    out = "PAGE: index.md | I | i\nPAGE: ../raw/a.md | A | a\n"
    assert [n for n, _, _ in w.parse_outline(out)] == ["index.md"]


OUTLINE = [("index.md", "Overview", "the topic"), ("costs.md", "Costs", "what things cost")]


def test_page_prompt_names_its_page_and_sees_every_source_and_page():
    p = w.build_page_prompt("bikes", [("b.md", "second"), ("a.md", "first")], OUTLINE, "costs.md")
    assert 'ONE page of a wiki from raw research sources: costs.md ("Costs")' in p
    assert "- [[index]] Overview: the topic" in p and "- [[costs]] Costs" in p
    assert p.index("--- SOURCE: b.md ---\nsecond") < p.index("--- SOURCE: a.md ---\nfirst")
    assert "This is the index page" not in p
    assert "This is the index page" in w.build_page_prompt("bikes", [("a.md", "x")], OUTLINE, "index.md")


def test_write_pages_makes_one_call_per_page_and_keeps_outline_order():
    calls = []

    def ask(prompt, model):
        calls.append(prompt)
        return "# Overview\nbody" if '"Overview"' in prompt else "# Costs\nbody"

    pages = w.write_pages("bikes", [("a.md", "x")], OUTLINE, "m", ask=ask)
    assert list(pages) == ["index.md", "costs.md"] and len(calls) == 2
    assert pages["costs.md"] == "# Costs\nbody"


def test_write_pages_refuses_an_empty_page():
    with pytest.raises(w.WikiError, match="costs.md empty"):
        w.write_pages("bikes", [("a.md", "x")], OUTLINE, "m",
                      ask=lambda p, m: "" if '"Costs"' in p else "# ok")


def test_is_text():
    assert w.is_text("plain notes æøå")
    assert not w.is_text("%PDF-1.7\x00\x01")


def test_rendered_page_is_marked_and_names_its_sources():
    page = w.render_page("# Hi", "claude-haiku-4-5", ["a.md", "b.md"], "2026-09-18 17:40")
    assert page.startswith("---\ngenerated_by: llm_wiki\n")
    assert "  - a.md\n  - b.md\n---\n\n# Hi\n" in page


def test_stale_pages_removes_only_generated_pages_it_did_not_rewrite():
    generated = w.render_page("old", "m", ["a.md"], "t")
    existing = {"index.md": generated, "gone.md": generated,
                "mine.md": "---\ntitle: by hand\n---\ngenerated_by: llm_wiki in the body"}
    assert w.stale_pages(existing, {"index.md": "new"}) == ["gone.md"]


def test_model_call_never_sees_the_metered_key(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-metered")
    monkeypatch.setattr(w.subprocess, "run", fake_run)
    assert w.ask_model("prompt", "claude-haiku-4-5") == "ok"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert seen["cmd"][:2] == ["claude", "-p"] and "--tools" in seen["cmd"]


def test_bad_topic_is_refused_before_any_vault_call(monkeypatch):
    monkeypatch.setattr(w, "_vault", lambda *a: pytest.fail("touched the vault"))
    with pytest.raises(w.WikiError, match="topic"):
        w.run("../escape")


def test_plan_prompt_asks_to_keep_the_existing_page_names():
    p = w.build_plan_prompt("bikes", [("a.md", "x")], keep=["route.md", "index.md"])
    assert "same name" in p and "index.md, route.md" in p and "--- SOURCE: a.md ---" in p
    assert "already has these pages" not in w.build_plan_prompt("bikes", [("a.md", "x")])


PLAN = ("PAGE: index.md | Overview | the topic | a.md\n"
        "PAGE: hiring.md | Hiring | hiring staff | a.md, invented.md\n")


def test_outline_still_parses_with_the_sources_field():
    assert w.parse_outline(PLAN) == [("index.md", "Overview", "the topic"),
                                     ("hiring.md", "Hiring", "hiring staff")]


def test_placement_keeps_known_sources_and_names_the_unplaced_one():
    placement = w.parse_placement(PLAN, ["a.md", "norway.md"])
    assert placement == {"index.md": ["a.md"], "hiring.md": ["a.md"]}
    assert w.unplaced(placement, ["a.md", "norway.md"]) == ["norway.md"]


def test_plan_asks_again_naming_the_missing_source_then_refuses():
    prompts = []

    def ask(prompt, model):
        prompts.append(prompt)
        return PLAN

    with pytest.raises(w.WikiError, match="norway.md on no page, twice"):
        w.plan("biz", [("a.md", "x"), ("norway.md", "y")], (), "m", out=lambda s: None, ask=ask)
    assert len(prompts) == 2 and "on no page: norway.md" in prompts[1]


def test_plan_accepts_a_second_plan_that_places_every_source():
    answers = iter([PLAN, PLAN + "PAGE: employer.md | Employer | duties | norway.md\n"])
    outline, placement = w.plan("biz", [("a.md", "x"), ("norway.md", "y")], (), "m",
                                out=lambda s: None, ask=lambda p, m: next(answers))
    assert [n for n, _, _ in outline] == ["index.md", "hiring.md", "employer.md"]
    assert placement["employer.md"] == ["norway.md"]


def test_page_prompt_names_the_sources_it_must_cite():
    p = w.build_page_prompt("biz", [("a.md", "x")], OUTLINE, "costs.md", ["a.md"])
    assert "must use and cite each of these sources: a.md" in p
    assert "must use" not in w.build_page_prompt("biz", [("a.md", "x")], OUTLINE, "costs.md")


def test_miscited_names_a_number_the_cited_source_does_not_hold():
    sources = [("books.md", "Keep records 5 years."), ("as.md", "A fee of up to NOK 69,940.")]
    pages = {"n.md": "Keep records for 5 years. [source: books.md]\n\n"
                     "The fee can be NOK 69 940. [source: books.md]\n\n"
                     "Illustrative: NOK 134,160 after tax. [source: as.md]"}
    assert w.miscited(sources, pages) == [
        ("n.md", "69 940", ["books.md"], ["as.md"]),
        ("n.md", "134,160", ["as.md"], []),
    ]


def test_miscited_accepts_a_number_any_cited_source_holds_and_ignores_lists():
    sources = [("a.md", "In 2008 it grew."), ("b.md", "Scores 2, 4, 4, 5.")]
    pages = {"p.md": "It grew in 2008 [source: b.md, a.md]; scores were 2, 4, 4, 5. [source: b.md]",
             "q.md": "Uncited 999 is not judged."}
    assert w.miscited(sources, pages) == []


def test_uncited_finds_a_source_no_page_cites():
    pages = {"index.md": "x [source: a.md] y [source: b.md, old-a.md]"}
    assert w.uncited(["a.md", "b.md", "norway.md", "c.md"], pages) == ["norway.md", "c.md"]
    # a name that is only a suffix of a cited one is not cited
    assert w.uncited(["a.md"], {"p": "[source: old-a.md]"}) == ["a.md"]


INDEX = """---
generated_by: llm_wiki
model: claude-haiku-4-5
generated: 2026-09-19 12:21
sources:
  - a.md
  - b.md
---

# Topic
"""


def test_front_sources_reads_stamp_and_sources():
    assert w.front_sources(INDEX) == ("2026-09-19 12:21", ["a.md", "b.md"])


def test_topic_is_current_when_raw_did_not_change_since_the_stamp():
    assert w.why_stale(INDEX, ["a.md", "b.md"], [("2026-09-19 12:20", "a.md")]) is None


def test_a_file_dropped_in_raw_after_the_build_makes_it_stale():
    why = w.why_stale(INDEX, ["a.md", "b.md", "c.md"], [("2026-09-19 18:30", "c.md")])
    assert "c.md" in why


def test_a_new_file_in_the_stamp_minute_counts():
    assert "c.md" in w.why_stale(INDEX, ["a.md", "b.md", "c.md"], [("2026-09-19 12:21", "c.md")])


def test_a_built_source_written_in_the_stamp_minute_does_not_rebuild_again():
    # measured Cycle 1886: sources put at 18:39, build stamped 18:39, next run rebuilt
    assert w.why_stale(INDEX, ["a.md", "b.md"], [("2026-09-19 12:21", "b.md")]) is None


def test_a_source_gone_from_raw_makes_it_stale():
    assert "b.md" in w.why_stale(INDEX, ["a.md"], [])


def test_no_index_or_no_stamp_is_stale():
    assert w.why_stale(None, ["a.md"], []) == "no wiki yet"
    assert w.why_stale(INDEX.replace("generated: 2026-09-19 12:21\n", ""), ["a.md", "b.md"], [])


def test_raw_changes_parses_recent_and_keeps_deletions(monkeypatch):
    base = w.ROOT + "t/"
    out = ("[2 file(s) modified in the last 8h]\n"
           f"2026-09-19 18:30  {base}raw/new file.md\n"
           f"2026-09-19 18:31  {base}raw/old.md  [DELETED]\n")
    seen = {}

    def fake(*args):
        seen["args"] = args
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    monkeypatch.setattr(w, "_vault", fake)
    now = w.datetime(2026, 9, 19, 18, 40, tzinfo=w.OSLO)
    got = w.raw_changes(base, "2026-09-19 12:21", now=now)
    assert got == [("2026-09-19 18:30", "new file.md"), ("2026-09-19 18:31", "old.md")]
    assert seen["args"] == ("recent", "8", f"{base}raw/")


def test_raw_changes_refuses_an_incomplete_listing(monkeypatch):
    monkeypatch.setattr(w, "_vault", lambda *a: type("R", (), {
        "returncode": 0, "stdout": "[INCOMPLETE: capped]\n", "stderr": ""})())
    with pytest.raises(w.WikiError):
        w.raw_changes(w.ROOT + "t/", "2026-09-19 12:21")


def test_run_stale_rebuilds_only_stale_topics_and_counts_failures(monkeypatch):
    monkeypatch.setattr(w, "topics", lambda: ["fresh", "old", "broken"])
    verdicts = {"fresh": None, "old": "raw/ changed", "broken": "no wiki yet"}
    monkeypatch.setattr(w, "check_topic", lambda t: verdicts[t])
    built = []

    def fake_run(topic, model, out=print):
        if topic == "broken":
            raise w.WikiError("no text sources")
        built.append(topic)

    monkeypatch.setattr(w, "run", fake_run)
    lines = []
    assert w.run_stale(out=lines.append) == 1
    assert built == ["old"]
    assert "fresh: current" in lines
    assert any(l.startswith("broken: FAILED") for l in lines)


def test_plan_holds_an_existing_page_the_model_dropped_then_refuses():
    prompts = []

    def ask(prompt, model):
        prompts.append(prompt)
        return PLAN

    with pytest.raises(w.WikiError, match="dropped structures.md, twice"):
        w.plan("biz", [("a.md", "x")], ["index.md", "hiring.md", "structures.md"], "m",
               out=lambda s: None, ask=ask, hold=True)
    assert len(prompts) == 2 and "dropped these existing pages" in prompts[1]
    assert "structures.md" in prompts[1]


def test_plan_accepts_a_second_plan_that_keeps_the_page():
    answers = iter([PLAN, PLAN + "PAGE: structures.md | Structures | AS or ENK | a.md\n"])
    outline, _ = w.plan("biz", [("a.md", "x")], ["index.md", "structures.md"], "m",
                        out=lambda s: None, ask=lambda p, m: next(answers), hold=True)
    assert "structures.md" in [n for n, _, _ in outline]


def test_plan_without_hold_may_drop_a_page():
    calls = []
    outline, _ = w.plan("biz", [("a.md", "x")], ["index.md", "structures.md"], "m",
                        out=lambda s: None, ask=lambda p, m: calls.append(p) or PLAN)
    assert len(calls) == 1 and "structures.md" not in [n for n, _, _ in outline]
