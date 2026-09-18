import pytest

from tools import llm_wiki as w


def test_parse_pages_splits_on_page_lines():
    out = "=== PAGE: index.md ===\n# Topic\nsee [[a]]\n=== PAGE: a.md ===\nbody a\n"
    assert w.parse_pages(out) == {"index.md": "# Topic\nsee [[a]]", "a.md": "body a"}


@pytest.mark.parametrize("out, why", [
    ("just prose, no markers", "no '=== PAGE"),
    ("=== PAGE: a.md ===\nbody\n", "no index.md"),
    ("=== PAGE: index.md ===\nx\n=== PAGE: index.md ===\ny\n", "twice"),
    ("=== PAGE: index.md ===\n\n=== PAGE: a.md ===\nb\n", "empty"),
])
def test_parse_pages_refuses_a_partial_wiki(out, why):
    with pytest.raises(w.WikiError, match=why):
        w.parse_pages(out)


def test_page_name_outside_the_pattern_is_not_a_page():
    # A path in the name would write outside wiki/; it must not match at all.
    out = "=== PAGE: index.md ===\nx\n=== PAGE: ../raw/a.md ===\ny\n"
    assert list(w.parse_pages(out)) == ["index.md"]


def test_is_text():
    assert w.is_text("plain notes æøå")
    assert not w.is_text("%PDF-1.7\x00\x01")


def test_build_prompt_keeps_every_source_whole_and_in_order():
    p = w.build_prompt("bikes", [("b.md", "second"), ("a.md", "first")])
    assert "The topic is: bikes" in p
    assert p.index("--- SOURCE: b.md ---\nsecond") < p.index("--- SOURCE: a.md ---\nfirst")


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


def test_prompt_asks_to_keep_the_existing_page_names():
    p = w.build_prompt("bikes", [("a.md", "x")], keep=["route.md", "index.md"])
    assert "same name" in p and "index.md, route.md" in p
    assert "already has these pages" not in w.build_prompt("bikes", [("a.md", "x")])
