"""CLI over `agora_runner.catalog_build` -- print the catalog, write it, publish it.

The builder moved into `agora_runner/` on 2026-08-26 (Cycle 451) and this
file is what is left: argument parsing, and the names `tests/test_catalog.py`
imports. The move is not tidying. The runner image copies `agora_runner/`
and nothing else, so for as long as the builder lived here the only thing
that could ever regenerate the catalog was a cycle typing the command --
which is why `catalog.md` in the vault said "Cycle 448" and the page it
feeds printed a timestamp going stale. `agora_runner.catalog_refresh` runs
it on a timer inside the runner pod now, and it can only import what ships.

    python3 -m tools.catalog                      # print the catalog
    python3 -m tools.catalog --write catalog.md   # and write it as markdown
    python3 -m tools.catalog --publish            # and put it in the vault

Exit codes are the builder's: 0 if every source answered, 1 if one did not
and the coverage numbers are therefore suppressed rather than computed from
a partial read.
"""

from __future__ import annotations

import argparse
import sys

# Repo root on sys.path so `python3 tools/catalog.py` works and not only
# `-m`. See tests/test_tools_run_as_scripts.py.
import pathlib as _pathlib  # noqa: E402

sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.catalog_build import (  # noqa: E402,F401  -- re-exported for tests and callers
    APP_NAMESPACES,
    FRONT_MATTER,
    REPO_ONLY_KINDS,
    Service,
    Source,
    VAULT_PATH,
    attach_argocd,
    attach_claims,
    attach_urls,
    build,
    coverage,
    document,
    publish,
    read_argocd_apps,
    read_claims,
    read_ingresses,
    read_workloads,
    read_xr_kinds,
    render,
    services_from,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", metavar="PATH", help="also write the catalog as markdown to PATH")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="also write it to the vault at " + VAULT_PATH + ", frontmatter and all",
    )
    args = parser.parse_args(argv)

    if args.publish:
        text, status = publish()
        print(text)
        print(f"published: {VAULT_PATH}", file=sys.stderr)
    else:
        text, status = build()
        print(text)
    if args.write:
        with open(args.write, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"written: {args.write}", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
