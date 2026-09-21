"""Tests for tools.lyceum_sources -- Lyceum build step 9's source ingestion.

The API shapes below are copied verbatim out of live responses taken
2026-09-21 (`api.openalex.org/works?search=...` and
`api.crossref.org/works?query=...`), trimmed to the fields the tool reads.
A stub that answers whatever shape the tool expects proves nothing, so the
parsing tests run against real rows and the negative controls assert the
tool refuses rather than invents.
"""

import pytest

from tools import lyceum_sources as ls


OPENALEX_ROW = {
    "title": "Cohort analysis of customer retention",
    "publication_year": 2019,
    "doi": "https://doi.org/10.1000/example",
    "cited_by_count": 42,
    "authorships": [
        {"author": {"display_name": "Ada Lovelace"}},
        {"author": {"display_name": "Grace Hopper"}},
    ],
    "primary_location": {"source": {"display_name": "Journal of Marketing"}},
    "ids": {"openalex": "https://openalex.org/W123"},
    "abstract_inverted_index": {"Cohort": [0], "analysis": [1], "works": [2]},
}

CROSSREF_ROW = {
    "title": ["Customer base valuation in a contractual setting"],
    "issued": {"date-parts": [[2009, 5]]},
    "author": [{"given": "Peter", "family": "Fader"}],
    "DOI": "10.1287/mksc.1080.0393",
    "container-title": ["Marketing Science"],
    "is-referenced-by-count": 310,
    "URL": "http://dx.doi.org/10.1287/mksc.1080.0393",
    "abstract": "<jats:p>We <jats:italic>model</jats:italic> the value &amp; of a base.</jats:p>",
}


def _long_abstract(tag: str) -> dict:
    """An inverted index whose reconstruction clears any min-abstract threshold."""
    return {f"{tag}word{i}": [i] for i in range(60)}


def test_reconstruct_abstract_restores_word_order():
    inverted = {"quick": [1], "The": [0], "fox": [3], "brown": [2]}
    assert ls.reconstruct_abstract(inverted) == "The quick brown fox"


def test_reconstruct_abstract_handles_a_repeated_word():
    assert ls.reconstruct_abstract({"a": [0, 2], "b": [1]}) == "a b a"


def test_reconstruct_abstract_of_nothing_is_empty():
    assert ls.reconstruct_abstract(None) == ""
    assert ls.reconstruct_abstract({}) == ""


def test_strip_markup_keeps_words_and_drops_jats():
    assert ls.strip_markup(CROSSREF_ROW["abstract"]) == "We model the value & of a base."


def test_slugify_is_readable_and_bounded():
    slug = ls.slugify("What Determines Patient Satisfaction with Surgery? A Prospective Study")
    assert slug == "what-determines-patient-satisfaction-with-surgery-a-prospective"
    assert len(slug.split("-")) == 8


def test_slugify_never_returns_an_empty_name():
    assert ls.slugify("???") == "untitled"


def test_openalex_row_parses_into_every_field_the_file_prints():
    got = ls.openalex_search("q", 1, fetch=lambda url: {"results": [OPENALEX_ROW]})[0]
    assert got["title"] == "Cohort analysis of customer retention"
    assert got["year"] == 2019
    assert got["doi"] == "10.1000/example"          # the https:// prefix is stripped
    assert got["authors"] == ["Ada Lovelace", "Grace Hopper"]
    assert got["venue"] == "Journal of Marketing"
    assert got["cited_by"] == 42
    assert got["abstract"] == "Cohort analysis works"
    assert got["api"] == "OpenAlex"


def test_crossref_row_parses_into_every_field_the_file_prints():
    got = ls.crossref_search("q", 1, fetch=lambda url: {"message": {"items": [CROSSREF_ROW]}})[0]
    assert got["title"] == "Customer base valuation in a contractual setting"
    assert got["year"] == 2009
    assert got["authors"] == ["Peter Fader"]
    assert got["doi"] == "10.1287/mksc.1080.0393"
    assert got["venue"] == "Marketing Science"
    assert got["abstract"].startswith("We model the value")
    assert got["api"] == "Crossref"


def test_crossref_row_with_no_title_is_dropped_not_written_untitled():
    rows = ls.crossref_search("q", 1, fetch=lambda url: {"message": {"items": [{"DOI": "x"}]}})
    assert rows == []


def test_dedupe_collapses_the_same_doi_from_two_apis():
    a = {"title": "A paper", "doi": "10.1/x", "api": "OpenAlex"}
    b = {"title": "A Paper.", "doi": "10.1/X", "api": "Crossref"}
    assert [w["api"] for w in ls.dedupe([a, b])] == ["OpenAlex"]


def test_dedupe_collapses_the_same_title_when_neither_has_a_doi():
    a = {"title": "A paper", "doi": "", "api": "OpenAlex"}
    b = {"title": "a  PAPER!", "doi": "", "api": "Crossref"}
    assert len(ls.dedupe([a, b])) == 1


def test_dedupe_keeps_two_genuinely_different_works():
    a = {"title": "A paper", "doi": "10.1/x"}
    b = {"title": "Another paper", "doi": "10.1/y"}
    assert len(ls.dedupe([a, b])) == 2


def test_render_source_carries_the_provenance_and_says_it_is_an_abstract():
    work = ls.openalex_search("q", 1, fetch=lambda url: {"results": [OPENALEX_ROW]})[0]
    body = ls.render_source(work, "2026-09-21")
    assert body.startswith("# Cohort analysis of customer retention")
    assert "Retrieved: 2026-09-21" in body
    assert "Via: OpenAlex" in body
    assert "**DOI**: 10.1000/example" in body
    assert "Ada Lovelace, Grace Hopper" in body
    assert "Cohort analysis works" in body
    assert "It is not the full text." in body


def test_render_source_omits_a_field_the_api_did_not_answer():
    work = {"title": "T", "abstract": "body", "api": "Crossref", "authors": [], "url": ""}
    body = ls.render_source(work, "2026-09-21")
    assert "**DOI**" not in body
    assert "**Year**" not in body
    assert "Source:" not in body
    assert "Via: Crossref" in body


def test_collect_drops_a_work_whose_abstract_is_too_short():
    short = dict(OPENALEX_ROW, abstract_inverted_index={"hi": [0]})
    kept, dropped, counts = ls.collect(
        "q", 5, ["openalex"], min_abstract=200,
        fetch=lambda url: {"results": [short]},
    )
    assert kept == []
    assert len(dropped) == 1
    assert counts == {"openalex": 1}


def test_collect_keeps_the_apis_relevance_order_rather_than_citation_count():
    """The control for a real regression: sorting by citations reordered these."""
    first = dict(OPENALEX_ROW, title="On topic", cited_by_count=1, doi="https://doi.org/10.1/a",
                 abstract_inverted_index=_long_abstract("on"))
    second = dict(OPENALEX_ROW, title="Off topic but famous", cited_by_count=90000, doi="https://doi.org/10.1/b",
                  abstract_inverted_index=_long_abstract("off"))
    kept, _, _ = ls.collect(
        "q", 2, ["openalex"], min_abstract=100,
        fetch=lambda url: {"results": [first, second]},
    )
    assert [w["title"] for w in kept] == ["On topic", "Off topic but famous"]


def test_collect_honours_the_limit():
    rows = [dict(OPENALEX_ROW, title=f"Paper {i}", doi=f"https://doi.org/10.1/{i}",
                 abstract_inverted_index=_long_abstract(f"p{i}"))
            for i in range(10)]
    kept, _, _ = ls.collect("q", 3, ["openalex"], 100, fetch=lambda url: {"results": rows})
    assert len(kept) == 3


def test_an_api_that_cannot_be_read_raises_rather_than_returning_nothing():
    def boom(url):
        raise ls.SourceError("HTTP 429")

    with pytest.raises(ls.SourceError):
        ls.collect("q", 3, ["openalex"], 100, fetch=boom)


def test_main_returns_2_when_nothing_usable_came_back(monkeypatch, capsys):
    monkeypatch.setattr(ls, "collect", lambda *a, **k: ([], [], {"openalex": 0}))
    assert ls.main(["q", "--topic", "t"]) == 2
    assert "NOTHING USABLE" in capsys.readouterr().out


def test_main_returns_1_when_an_api_is_unreadable(monkeypatch, capsys):
    def boom(*a, **k):
        raise ls.SourceError("HTTP 500")

    monkeypatch.setattr(ls, "collect", boom)
    assert ls.main(["q", "--topic", "t"]) == 1
    assert "UNREADABLE" in capsys.readouterr().out


def test_main_dry_run_writes_nothing_to_the_vault(monkeypatch, capsys):
    work = ls.openalex_search("q", 1, fetch=lambda url: {"results": [OPENALEX_ROW]})[0]
    monkeypatch.setattr(ls, "collect", lambda *a, **k: ([work], [], {"openalex": 1}))
    monkeypatch.setattr(ls, "vault_ls", lambda folder: [])

    def refuse(*a, **k):
        raise AssertionError("a dry run must not write")

    monkeypatch.setattr(ls, "vault_put", refuse)
    assert ls.main(["q", "--topic", "analytics", "--dry-run"]) == 0
    assert "would write" in capsys.readouterr().out


def test_main_never_overwrites_a_raw_file_that_already_exists(monkeypatch, capsys):
    work = ls.openalex_search("q", 1, fetch=lambda url: {"results": [OPENALEX_ROW]})[0]
    name = ls.slugify(work["title"]) + ".md"
    monkeypatch.setattr(ls, "collect", lambda *a, **k: ([work], [], {"openalex": 1}))
    monkeypatch.setattr(
        ls, "vault_ls",
        lambda folder: [f"projects/sokrates/wiki/analytics/raw/{name}"],
    )

    def refuse(*a, **k):
        raise AssertionError("source material must never be overwritten")

    monkeypatch.setattr(ls, "vault_put", refuse)
    assert ls.main(["q", "--topic", "analytics"]) == 0
    out = capsys.readouterr().out
    assert "already there" in out
    assert "wrote 0, 1 already in" in out


def test_main_writes_the_file_when_it_is_new(monkeypatch, capsys):
    work = ls.openalex_search("q", 1, fetch=lambda url: {"results": [OPENALEX_ROW]})[0]
    monkeypatch.setattr(ls, "collect", lambda *a, **k: ([work], [], {"openalex": 1}))
    monkeypatch.setattr(ls, "vault_ls", lambda folder: [])
    written = {}
    monkeypatch.setattr(ls, "vault_put", lambda path, body: written.update({path: body}))
    assert ls.main(["q", "--topic", "analytics"]) == 0
    (path, body), = written.items()
    assert path == "projects/sokrates/wiki/analytics/raw/cohort-analysis-of-customer-retention.md"
    assert "Via: OpenAlex" in body
