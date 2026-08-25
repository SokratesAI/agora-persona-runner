"""The `/catalog` page: parsing `nova/catalog.md` back into rows.

The fixtures here are built by calling `tools.catalog.render` rather than
by pasting its output into this file. That is the point of the test: the
writer and the reader are in one repo and nothing else connects them, so
a column added to `render` has to fail here rather than render as a blank
cell on his phone.
"""

import json
from unittest.mock import patch

from agora_runner import nova_catalog, nova_site
from agora_runner.nova_catalog import catalog_page, parse_catalog
from tools.catalog import Service, render


def _service(name, namespace, **kw):
    fields = {
        "name": name,
        "namespace": namespace,
        "kind": "Deployment",
        "image": "ghcr.io/sokratesai/" + name + ":latest",
        "repo_claim": "",
        "argocd_app": "",
        "url": "",
        "ready": True,
        "wanted": 1,
    }
    fields.update(kw)
    return Service(**fields)


def _rendered():
    services = [
        _service("agora", "agents", repo_claim="GitHubRepoPolicy",
                 argocd_app="agora-config", url="https://agora.tailc83eb3.ts.net"),
        _service("redis", "agents"),
        _service("ollama", "infra", ready=False, wanted=0),
        _service("whatsapp-bridge", "infra", ready=False, wanted=1,
                 url="https://whatsapp-bridge.tailc83eb3.ts.net"),
    ]
    orphans = ["argocd/argocd-tailscale -> argocd-server (argocd.tailc83eb3.ts.net)"]
    return render(services, orphans, [])


def test_every_row_the_writer_emits_comes_back_as_a_service():
    page = catalog_page(parse_catalog(_rendered()))
    assert [s["name"] for s in page["services"]] == [
        "agora", "redis", "ollama", "whatsapp-bridge"
    ]
    assert page["total"] == 4


def test_the_three_states_the_writer_can_emit_stay_three_states():
    """`off` and `NO` are opposite facts and must not collapse.

    One is a workload scaled to zero on purpose, the other is a workload
    that is wanted and not there. A page that drew them the same way
    would report a deliberate shutdown as an outage every time, which is
    the failure that makes a status page worth ignoring.
    """
    rows = {s["name"]: s["status"] for s in parse_catalog(_rendered())["services"]}
    assert rows["agora"] == "up"
    assert rows["ollama"] == "off"
    assert rows["whatsapp-bridge"] == "down"
    page = catalog_page(parse_catalog(_rendered()))
    assert page["down"] == 1
    assert page["off"] == 1


def test_a_link_cell_arrives_split_into_host_and_href():
    rows = {s["name"]: s for s in parse_catalog(_rendered())["services"]}
    assert rows["agora"]["host"] == "agora.tailc83eb3.ts.net"
    assert rows["agora"]["url"] == "https://agora.tailc83eb3.ts.net"
    # An em dash is the writer's "nothing", and it must not reach the page
    # as a character the client has to know the meaning of.
    assert rows["redis"]["url"] is None
    assert rows["redis"]["claim"] is None
    assert rows["redis"]["deployedBy"] is None


def test_the_headline_splits_into_the_claim_and_its_qualification():
    page = parse_catalog(_rendered())
    # 0, not 1: `agora`'s claim is a `GitHubRepoPolicy`, which orders the
    # repo and not the workload -- the distinction the writer's own
    # `REPO_ONLY_KINDS` exists to make, and the reason the two halves of
    # this sentence are separate fields on the page.
    assert page["headline"] == "0 of 4 running services are composed by a claim."
    assert page["detail"].startswith("1 of them have a source repo")
    assert "**" not in page["headline"] and "**" not in page["detail"]
    assert page["incomplete"] is False


def test_the_prose_arrives_without_its_markdown_markers():
    """The page draws text, and the catalog's prose is written as markdown.

    Found by opening the real page and looking at it: the qualification
    rendered as `*source repo*` and a backticked `GitHubService`, with
    the punctuation on screen. Every test above asserts on the string and
    the string was right -- what was wrong was that nothing rendered it.
    """
    page = parse_catalog(_rendered())
    assert "*" not in page["detail"] and "`" not in page["detail"]
    assert "source repo that was ordered as one" in page["detail"]
    assert "a GitHubService writes the" in page["detail"]


def test_doors_are_read_as_their_own_list():
    page = parse_catalog(_rendered())
    assert page["doors"] == [
        "argocd/argocd-tailscale -> argocd-server (argocd.tailc83eb3.ts.net)"
    ]


def test_an_incomplete_catalog_says_so_and_keeps_its_unread_sources():
    """The writer suppresses the coverage number when a source failed.

    If the page did not notice, it would draw a partial table under a
    confident heading -- and a catalog that is quietly short is worse
    than no catalog, because nothing about it looks wrong.
    """
    markdown = render([_service("agora", "agents")], ["some-ingress"],
                      ["namespaces: Forbidden"])
    page = parse_catalog(markdown)
    assert page["incomplete"] is True
    assert page["unreadable"] == ["namespaces: Forbidden"]
    # `render` withholds the orphan section on a partial read; the page
    # must not invent one from the argument it was passed.
    assert page["doors"] == []
    assert [s["name"] for s in page["services"]] == ["agora"]


def test_the_provenance_line_is_read_off_the_frontmatter():
    """When the catalog was last built is the one thing the page owes him.

    Step 3 of the roadmap is an hourly refresh; until it exists the file
    is as old as the last cycle that ran the tool, and the page has to be
    able to say so rather than presenting an hours-old picture as live.
    """
    markdown = (
        "---\n"
        "maintenance: Generated by `python3 -m tools.catalog`. "
        "Cycle 448 (last regenerated 2026-08-26 00:48 Oslo).\n"
        "---\n"
    ) + _rendered()
    page = parse_catalog(markdown)
    assert page["cycle"] == 448
    assert page["regenerated"] == "2026-08-26 00:48 Oslo"


def test_no_catalog_yet_is_an_empty_page_not_an_error():
    page = catalog_page(parse_catalog(""))
    assert page["missing"] is True
    assert page["services"] == []
    assert page["total"] == 0


def test_the_page_route_and_the_api_are_both_wired():
    """A parser nothing serves is a parser nobody reads.

    `/catalog` has to be in `PAGE_ROUTES` -- that list is what `do_GET`
    matches and what `site_check` walks -- and `/api/catalog` has to be a
    path the handler answers. Cycle 441 shipped a page whose writes came
    back "not found" with every test green, because the route list and
    the handler were two lists and only one of them had been edited.
    """
    assert "/catalog" in nova_site.PAGE_ROUTES
    source = open(nova_site.__file__).read()
    assert '"/api/catalog"' in source


def test_the_endpoint_serves_what_the_parser_built():
    with patch.object(nova_site, "catalog_markdown", return_value=_rendered()):
        payload = nova_site.catalog_payload()
    body = json.dumps(payload)
    assert '"name": "agora"' in body
    assert payload["total"] == 4


def test_the_path_is_the_one_the_tool_writes():
    """One constant, not two.

    `tools.catalog` is run with `--write <path>` by a cycle, so the two
    halves cannot be checked by imports alone -- what this asserts is
    that the page reads the location the loop publishes to, which is
    named in the roadmap and in `prompt.md`'s wrap-up.
    """
    assert nova_catalog.CATALOG_PATH == (
        "projects/sokrates/projects/agora/nova/catalog.md"
    )
