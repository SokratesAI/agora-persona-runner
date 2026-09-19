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
