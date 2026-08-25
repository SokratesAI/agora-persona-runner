"""Sources the Sokrates Post scrapes every night that have never once worked.

Cycle 405. The curated desk came back on 2026-08-22 -- the generator runs
on a hosted model now and writes real articles against the configured
topics again. So the paper is being edited. What it is not doing is
noticing that some of the sources it is told to read do not exist.

`/api/source-stats` already counts, per category and per host, how many
runs happened, how many candidates were found, how many articles were
written and how many fetches errored. Everything below is read off that
blob; nothing new is measured. What was missing is that nobody reads it.
Four RSS feeds have failed on **82 consecutive runs** and eighteen more
hosts have failed every run they have ever had, and the only place that
fact appears is a 43KB JSON document behind a stats page.

    python3 -m tools.newspaper_dead_sources

The distinction the output keeps is the one that decides whether there is
anything to do. A host that errors every run and has written nothing is a
**dead source**: a wrong URL, a domain that no longer resolves, or a site
that refuses this crawler outright. A host that fetches fine and still
writes nothing is a **quiet source** -- it works, it just has not had
anything worth printing, which is a normal thing for a niche feed to do
and is not a defect. Collapsing the two would report the whole long tail
of the paper as broken.

Exit 2 means at least one dead source. Exit 1 means the stats could not
be read, which is not the same as a clean sweep and is printed as such.
Exit 0 means every configured source has either worked or failed for a
reason that is not the fetch.

**Why a run threshold rather than "errored at least once".** A source
that failed once last night is a transient; a source that has failed
every run for eleven weeks is config. The floor is `--min-runs` (default
4) so that a source added yesterday cannot be called dead on one bad
night, and the report prints `runs` beside `errors` so the judgement is
visible rather than buried in the threshold.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://newspaper.agents.svc.cluster.local/api/source-stats"


def fetch_stats(url: str, timeout: float = 20.0) -> dict:
    """Return the parsed `/api/source-stats` document."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def classify(stats: dict, min_runs: int = 4) -> tuple[list[dict], list[dict]]:
    """Split every (category, mode, host) entry into dead and quiet.

    Dead: it has run at least `min_runs` times, has never written an
    article, and every fetch it has made has failed. Quiet: it has written
    nothing but its fetches are not the reason.

    **"Every fetch failed" is a proxy and it is worth naming.** The blob
    counts runs and fetch errors but not fetches, and a run can fetch more
    than once, so `errors >= runs` is where the two counters cross rather
    than a proof that nothing ever succeeded. `last_fetch_ok` is the one
    direct observation available and it covers only the most recent run, so
    it is used to *rule out* a dead verdict and never to reach one: a
    source whose last fetch worked is not dead however its counters read.
    """
    categories = (stats.get("source_stats") or {}).get("categories") or {}
    dead: list[dict] = []
    quiet: list[dict] = []
    for category, modes in sorted((categories or {}).items()):
        if not isinstance(modes, dict):
            continue
        for mode, hosts in sorted(modes.items()):
            if not isinstance(hosts, dict):
                continue
            for host, entry in sorted(hosts.items()):
                if not isinstance(entry, dict):
                    continue
                runs = entry.get("runs") or 0
                written = entry.get("written") or 0
                errors = entry.get("fetch_errors") or 0
                if written:
                    continue
                row = {
                    "category": category,
                    "mode": mode,
                    "host": host,
                    "runs": runs,
                    "errors": errors,
                    "last_run_at": entry.get("last_run_at") or "",
                }
                # The floor gates the *dead* verdict only. A source with no
                # errors is unambiguous however few runs it has, and dropping
                # it from both lists would hide it rather than judge it.
                fetched_ok = entry.get("last_fetch_ok")
                is_dead = (
                    runs >= max(min_runs, 1)
                    and errors >= runs
                    and fetched_ok is not True
                )
                (dead if is_dead else quiet).append(row)
    dead.sort(key=lambda r: (-r["errors"], r["category"], r["host"]))
    quiet.sort(key=lambda r: (r["category"], r["host"]))
    return dead, quiet


def render(dead: list[dict], quiet: list[dict], min_runs: int) -> str:
    lines: list[str] = []
    if dead:
        lines.append(
            f"DEAD SOURCES — {len(dead)} configured source(s) have errored on every "
            f"run and never written an article."
        )
        lines.append(
            "  Read `last run` before assuming one is still being retried: a source "
            "whose job is suspended stops being attempted and its counter freezes."
        )
        for row in dead:
            lines.append(
                f"  {row['errors']:4} errors / {row['runs']:4} runs  "
                f"{row['mode']:6} {row['host']}"
            )
            lines.append(f"       category: {row['category']}   last run: {row['last_run_at']}")
    else:
        lines.append(
            f"No dead sources: every source with at least {min_runs} runs has either "
            "written something or fetched cleanly."
        )
    if quiet:
        lines.append("")
        lines.append(
            f"Quiet, not broken — {len(quiet)} source(s) fetch fine and have written "
            "nothing. Normal for a niche feed; listed so it is not confused with the above."
        )
        for row in quiet:
            lines.append(
                f"  {row['errors']:4} errors / {row['runs']:4} runs  "
                f"{row['mode']:6} {row['host']}  ({row['category']})"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--min-runs", type=int, default=4)
    args = parser.parse_args(argv)

    try:
        stats = fetch_stats(args.url)
        dead, quiet = classify(stats, min_runs=args.min_runs)
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        TimeoutError,
    ) as exc:
        # `classify` is inside the guard on purpose. A payload that arrived
        # fine and is shaped wrong is still "I could not read this", and the
        # sibling tools in this folder all funnel a deeper failure through
        # one point rather than letting a traceback out of `main`.
        print(f"COULD NOT READ {args.url}: {exc}", file=sys.stderr)
        print("This is no instrument, not a clean sweep.", file=sys.stderr)
        return 1
    print(render(dead, quiet, args.min_runs))
    return 2 if dead else 0


if __name__ == "__main__":
    raise SystemExit(main())
