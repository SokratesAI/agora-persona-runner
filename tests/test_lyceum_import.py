import pytest

from tools import lyceum_import as li


FRONTMATTER = (
    "---\n"
    "generated_by: llm_wiki\n"
    "generated: 2026-09-20 11:30\n"
    "sources:\n"
    "  - a.md\n"
    "  - b.md\n"
    "---\n"
    "# A Title\n\nbody text\n"
)


def test_split_frontmatter_reads_a_list_under_an_empty_scalar():
    meta, body = li.split_frontmatter(FRONTMATTER)
    assert meta["sources"] == ["a.md", "b.md"]
    assert meta["generated"] == "2026-09-20 11:30"
    assert body.startswith("# A Title")


def test_split_frontmatter_leaves_a_document_without_one_alone():
    meta, body = li.split_frontmatter("# Just a page\n\ntext\n")
    assert meta == {}
    assert body == "# Just a page\n\ntext\n"


def test_cited_sources_reads_the_body_not_the_frontmatter():
    # The generator puts the topic's whole source list in every page's
    # frontmatter, so a chapter's real sources are only in its citations.
    meta, body = li.split_frontmatter(
        FRONTMATTER.replace("body text",
                            "one [source: b.md] two [source: c.md] three [source: b.md]")
    )
    assert meta["sources"] == ["a.md", "b.md"]
    assert li.cited_sources(body) == ["b", "c"]


def test_cited_sources_is_empty_when_a_page_cites_nothing():
    assert li.cited_sources("plain prose with no citation at all") == []


def test_title_falls_back_to_the_slug_when_a_page_has_no_heading():
    assert li.title_of("no heading here", "cohort-analysis") == "cohort-analysis"
    assert li.title_of("# Cohort Analysis\n", "x") == "Cohort Analysis"


def test_source_url_and_retrieved_come_off_the_raw_header():
    body = "# T\n\nSource: https://en.wikipedia.org/wiki/X\nRetrieved: 2026-09-18\n"
    assert li.source_url(body) == "https://en.wikipedia.org/wiki/X"
    assert li.source_retrieved(body) == "2026-09-18"
    assert li.source_url("# T\n\nno header\n") is None


def _docs():
    return [
        {"_id": "course:t", "type": "course", "contentHash": "aaa"},
        {"_id": "chapter:t:one", "type": "chapter", "contentHash": "bbb"},
    ]


def test_import_writes_only_what_changed_and_carries_the_rev(monkeypatch):
    monkeypatch.setattr(li, "existing", lambda ids: {
        "course:t": ("1-old", "aaa"),          # unchanged
        "chapter:t:one": ("4-old", "different"),  # changed
    })
    sent = {}

    def fake(method, path, body=None):
        sent["method"], sent["path"], sent["body"] = method, path, body
        return 201, [{"ok": True, "id": d["_id"]} for d in body["docs"]]

    monkeypatch.setattr(li, "couch_request", fake)
    written, unchanged = li.import_docs(_docs())

    assert unchanged == 1
    assert [d["_id"] for d in written] == ["chapter:t:one"]
    assert sent["body"]["docs"][0]["_rev"] == "4-old"
    assert sent["path"] == "lyceum/_bulk_docs"


def test_a_second_import_of_the_same_vault_writes_nothing(monkeypatch):
    monkeypatch.setattr(li, "existing", lambda ids: {
        d["_id"]: ("1-x", d["contentHash"]) for d in _docs()
    })
    calls = []
    monkeypatch.setattr(li, "couch_request",
                        lambda *a, **k: calls.append(a) or (201, []))
    written, unchanged = li.import_docs(_docs())
    assert (written, unchanged) == ([], 2)
    assert calls == []


def test_a_new_document_is_written_without_a_rev(monkeypatch):
    monkeypatch.setattr(li, "existing", lambda ids: {})
    sent = {}
    monkeypatch.setattr(li, "couch_request", lambda m, p, b=None: (
        sent.update(body=b), (201, [{"ok": True} for _ in b["docs"]]))[1])
    written, unchanged = li.import_docs(_docs())
    assert unchanged == 0
    assert all("_rev" not in d for d in sent["body"]["docs"])


def test_a_refused_document_raises_rather_than_reporting_a_clean_import(monkeypatch):
    monkeypatch.setattr(li, "existing", lambda ids: {})
    monkeypatch.setattr(li, "couch_request", lambda m, p, b=None: (
        201, [{"ok": True, "id": "course:t"},
              {"id": "chapter:t:one", "error": "conflict"}]))
    with pytest.raises(li.LyceumImportError, match="refused"):
        li.import_docs(_docs())


def test_dry_run_reads_nothing_and_writes_nothing(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a dry run must not touch CouchDB")

    monkeypatch.setattr(li, "couch_request", refuse)
    monkeypatch.setattr(li, "existing", refuse)
    written, unchanged = li.import_docs(_docs(), dry_run=True)
    assert len(written) == 2 and unchanged == 0


def test_garmin_routes_is_not_offered_as_a_course(monkeypatch):
    monkeypatch.setattr(li, "_ls", lambda prefix: [
        f"{li.WIKI_ROOT}analytics/wiki/index.md",
        f"{li.WIKI_ROOT}analytics/raw/a.md",
        f"{li.WIKI_ROOT}garmin-routes/wiki/index.md",
        f"{li.WIKI_ROOT}garmin-routes/raw/b.md",
    ])
    assert li.topics() == ["analytics"]


def test_a_folder_with_only_raw_files_is_not_a_course(monkeypatch):
    # raw/ on its own is research nobody has written up yet.
    monkeypatch.setattr(li, "_ls", lambda prefix: [
        f"{li.WIKI_ROOT}analytics/wiki/index.md",
        f"{li.WIKI_ROOT}half-done/raw/a.md",
    ])
    assert li.topics() == ["analytics"]


def test_a_course_names_its_chapters_in_reading_order(monkeypatch):
    pages = {
        f"{li.WIKI_ROOT}t/wiki/index.md": "---\ngenerated: x\n---\n# T Overview\nspine\n",
        f"{li.WIKI_ROOT}t/wiki/beta.md": "# Beta\n[source: r.md]\n",
        f"{li.WIKI_ROOT}t/wiki/alpha.md": "# Alpha\n",
        f"{li.WIKI_ROOT}t/raw/r.md": "# R\nSource: https://x/\n",
    }
    monkeypatch.setattr(li, "_ls", lambda prefix: list(pages))
    monkeypatch.setattr(li, "_get", lambda path: pages[path])

    docs = li.build_wiki_course("t")
    course = next(d for d in docs if d["type"] == "course")
    assert course["chapterIds"] == ["chapter:t:alpha", "chapter:t:beta"]
    assert course["sourceIds"] == ["source:t:r"]
    assert course["title"] == "T Overview"
    assert [d["order"] for d in docs if d["type"] == "chapter"] == [0, 1]

    beta = next(d for d in docs if d["_id"] == "chapter:t:beta")
    assert beta["sourceIds"] == ["source:t:r"]
    ids = {d["_id"] for d in docs}
    assert all(s in ids for d in docs if d["type"] == "chapter" for s in d["sourceIds"])


def test_a_topic_with_no_index_is_refused_rather_than_half_imported(monkeypatch):
    monkeypatch.setattr(li, "_ls", lambda prefix: [f"{li.WIKI_ROOT}t/wiki/alpha.md"])
    with pytest.raises(li.LyceumImportError, match="no wiki/index.md"):
        li.build_wiki_course("t")


def test_the_quiz_folder_is_not_imported_as_a_chapter_or_a_source(monkeypatch):
    # quiz/ is build step 8's input, not reading material.
    pages = {
        f"{li.A9S_ROOT}_context.md": "# a9s\ncontext\n",
        f"{li.A9S_ROOT}theory/01-overview.md": "# Overview\n",
        f"{li.A9S_ROOT}resources/curated-sources.md": "# Sources\n",
        f"{li.A9S_ROOT}quiz/active-recall.md": "# Quiz\n",
    }
    monkeypatch.setattr(li, "_ls", lambda prefix: list(pages))
    monkeypatch.setattr(li, "_get", lambda path: pages[path])

    docs = li.build_a9s_course()
    assert [d["_id"] for d in docs if d["type"] == "chapter"] == ["chapter:a9s:01-overview"]
    assert [d["_id"] for d in docs if d["type"] == "source"] == ["source:a9s:curated-sources"]
    assert not any("quiz" in d["_id"] for d in docs)


def test_no_claim_document_is_written_before_extraction_exists(monkeypatch):
    # The spec requires claim.status from the first migration; writing claim
    # documents now would be exactly the retrofit it forbids.
    pages = {
        f"{li.WIKI_ROOT}t/wiki/index.md": "# T\nspine\n",
        f"{li.WIKI_ROOT}t/wiki/alpha.md": "# Alpha\n[source: r.md]\n",
        f"{li.WIKI_ROOT}t/raw/r.md": "# R\n",
    }
    monkeypatch.setattr(li, "_ls", lambda prefix: list(pages))
    monkeypatch.setattr(li, "_get", lambda path: pages[path])
    assert {d["type"] for d in li.build_wiki_course("t")} == {"course", "chapter", "source"}


def test_couch_request_refuses_to_run_without_the_bridge_credentials(monkeypatch):
    monkeypatch.delenv("CDB_BASE", raising=False)
    with pytest.raises(li.LyceumImportError, match="bridge pod"):
        li.couch_request("GET", "lyceum")
