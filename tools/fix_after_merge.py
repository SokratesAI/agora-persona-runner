"""How often did a PR I merged need a fix within 48 hours?

Idea #320: *"generation is no longer the bottleneck, verification is. I am my
own only reviewer (rule 1), and I have no number for how well that works."*
This is that number, read from GitHub alone: every PR merged in the org in
the window, and for each one whether a later PR merged within 48 hours that
touches one of the same files and reads as a repair.

**Two numbers, because the match is fuzzy and one number would hide which way
it is wrong.**

- The **floor** counts a PR only when a later PR names it outright -- its
  title or body says `#<n>`, or its title starts `Revert` and quotes the
  earlier title -- *and* that later title has a repair word in it (fix,
  revert, restore, repair, undo, hotfix, regression, broke). A repair that
  never says what it repairs is invisible to it.
- The **ceiling** also takes a later PR that only shares a changed file and
  has a repair word in its title. It catches the unnamed repairs and also
  "fix an unrelated old bug in the same file", so it overcounts, most of all
  on a file every cycle edits.

A third line, **came back**, counts any later PR that names the earlier one
at all. It is not a repair rate -- "slice 2 of #n" is planned work -- and is
printed beside the two so that the first cut of this tool, which counted it
as the floor and read 18.9%, is not mistaken for the answer.

**It counts every merged PR in the org, not only mine**, and cannot do
otherwise from GitHub: my PRs and Sokrates' are opened and merged by the same
`sokrates-ai-user` account (measured on the last 40 merges in three repos, 119
of 120). Dependabot is the only other author seen, and it is rare.

The truth is between the floor and the ceiling. Every pair behind the ceiling is printed with both
titles, so a reader can judge any one of them in a second rather than trust
the rate.

Exit 0 once the numbers are printed; 1 when GitHub could not be read, which
never reads as a clean rate.

    python3 -m tools.fix_after_merge [--days 30] [--hours 48] [--json]
"""

import argparse
import datetime
import json
import re
import subprocess
import sys

ORG = "SokratesAI"

#: "fixed" and "restore point" are left out on purpose: measured on the first
#: 30-day run, "instead of a fixed table" made 5 of 6 sampled marcus pairs and
#: "after a data-level restore point" named a planned upgrade as a repair.
REPAIR_WORDS = re.compile(
    r"\b(fix(es)?|revert(s|ed)?|restore[sd]?(?! point)|repair(s|ed)?|"
    r"undo(es)?|hotfix|regress(ion|ed)?|broke|broken)\b",
    re.IGNORECASE,
)

QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: 50, after: $after,
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body mergedAt updatedAt
        files(first: 100) { nodes { path } }
      }
    }
  }
}
"""


def _gh(args, runner=subprocess.run):
    done = runner(["gh", *args], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {done.stderr.strip()[:300]}")
    return done.stdout


def _ts(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def list_repos(runner=subprocess.run):
    out = _gh(["repo", "list", ORG, "--no-archived", "--limit", "200",
               "--json", "name"], runner)
    return sorted(r["name"] for r in json.loads(out))


def merged_prs(repo, since, runner=subprocess.run):
    """Every PR in `repo` merged at or after `since`, with its changed files.

    Ordered by UPDATED_AT because GitHub offers no merged-at order; a PR
    merged in the window was updated at or after its merge, so paging stops
    once a whole page was last updated before `since`.
    """
    prs, after = [], None
    while True:
        args = ["api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={ORG}",
                "-F", f"name={repo}"]
        if after:
            args += ["-F", f"after={after}"]
        page = json.loads(_gh(args, runner))["data"]["repository"]["pullRequests"]
        for node in page["nodes"]:
            if node["mergedAt"] and _ts(node["mergedAt"]) >= since:
                prs.append({
                    "repo": repo,
                    "number": node["number"],
                    "title": node["title"],
                    "body": node["body"] or "",
                    "merged": _ts(node["mergedAt"]),
                    "files": {f["path"] for f in node["files"]["nodes"]},
                })
        oldest = min((_ts(n["updatedAt"]) for n in page["nodes"]), default=since)
        if not page["pageInfo"]["hasNextPage"] or oldest < since:
            return prs
        after = page["pageInfo"]["endCursor"]


def names(later, earlier):
    """Does `later` point at `earlier` by number, or revert it by title?"""
    text = f"{later['title']}\n{later['body']}"
    if re.search(rf"(?<![\w/-])#{earlier['number']}\b", text):
        return True
    return (later["title"].lower().startswith("revert")
            and earlier["title"].lower() in later["title"].lower())


def pair_up(prs, hours):
    """Each earlier PR with the first later PR that repaired it, per bound.

    Returns `{(repo, number): {"floor", "ceiling", "came_back": pr|None}}`
    for every PR; the ceiling's match is set whenever the floor's is.
    """
    window = datetime.timedelta(hours=hours)
    by_repo = {}
    for pr in prs:
        by_repo.setdefault(pr["repo"], []).append(pr)
    result = {}
    for repo_prs in by_repo.values():
        repo_prs.sort(key=lambda p: p["merged"])
        for i, earlier in enumerate(repo_prs):
            floor = ceiling = came_back = None
            for later in repo_prs[i + 1:]:
                if later["merged"] - earlier["merged"] > window:
                    break
                named = names(later, earlier)
                repair = REPAIR_WORDS.search(later["title"])
                if named and came_back is None:
                    came_back = later
                if named and repair and floor is None:
                    floor = later
                if ceiling is None and repair and (
                        named or earlier["files"] & later["files"]):
                    ceiling = later
            result[(earlier["repo"], earlier["number"])] = {
                "floor": floor, "ceiling": ceiling, "came_back": came_back}
    return result


def summarise(prs, pairs, now, hours):
    """Rates over the PRs old enough to have had their whole window."""
    cutoff = now - datetime.timedelta(hours=hours)
    judged = [p for p in prs if p["merged"] <= cutoff]
    keys = [(p["repo"], p["number"]) for p in judged]
    floor = sum(1 for k in keys if pairs[k]["floor"])
    ceiling = sum(1 for k in keys if pairs[k]["ceiling"])
    came_back = sum(1 for k in keys if pairs[k]["came_back"])
    return {
        "came_back": came_back,
        "judged": len(judged),
        "too_recent": len(prs) - len(judged),
        "floor": floor,
        "ceiling": ceiling,
    }


def _pct(n, d):
    return f"{100 * n / d:.1f}%" if d else "n/a"


def main(argv=None, runner=subprocess.run, now=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=args.days)
    try:
        repos = list_repos(runner)
        prs = [pr for repo in repos for pr in merged_prs(repo, since, runner)]
    except (RuntimeError, KeyError, TypeError, ValueError) as err:
        print(f"CANNOT READ GITHUB: {err}")
        return 1
    if not prs:
        print(f"CANNOT READ GITHUB: zero merged PRs in {len(repos)} repos over "
              f"{args.days} days, which is no instrument rather than no rework")
        return 1
    pairs = pair_up(prs, args.hours)
    s = summarise(prs, pairs, now, args.hours)
    if args.json:
        print(json.dumps({**s, "days": args.days, "hours": args.hours,
                          "repos": len(repos)}))
        return 0
    print(f"FIX WITHIN {args.hours}H OF MERGE -- {s['judged']} PRs merged in "
          f"the last {args.days} days across {len(repos)} repos "
          f"({s['too_recent']} newer than {args.hours}h left out: their "
          f"window is still open)")
    print(f"  floor   {s['floor']:4d}  {_pct(s['floor'], s['judged']):>6}  "
          "a later PR names it (#n, or Revert + title) with a repair word")
    print(f"  ceiling {s['ceiling']:4d}  {_pct(s['ceiling'], s['judged']):>6}  "
          "or: a later PR shares a file and has a repair word in its title")
    print(f"  came back {s['came_back']:2d}  {_pct(s['came_back'], s['judged']):>6}  "
          "a later PR names it at all -- includes planned follow-ups, not a rate")
    per_repo = {}
    for pr in prs:
        k = (pr["repo"], pr["number"])
        if pr["merged"] <= now - datetime.timedelta(hours=args.hours):
            row = per_repo.setdefault(pr["repo"], [0, 0, 0])
            row[0] += 1
            row[1] += bool(pairs[k]["floor"])
            row[2] += bool(pairs[k]["ceiling"])
    print("\nPER REPO (judged / floor / ceiling):")
    for repo, (n, f, c) in sorted(per_repo.items(), key=lambda kv: -kv[1][0]):
        print(f"  {repo:32s} {n:4d} {f:4d} {c:4d}")
    print("\nPAIRS BEHIND THE CEILING (F = also in the floor):")
    for pr in sorted(prs, key=lambda p: p["merged"]):
        hit = pairs[(pr["repo"], pr["number"])]
        if hit["ceiling"] and pr["merged"] <= now - datetime.timedelta(hours=args.hours):
            later = hit["ceiling"]
            gap = (later["merged"] - pr["merged"]).total_seconds() / 3600
            print(f"  {'F' if hit['floor'] else ' '} {pr['repo']}#{pr['number']} "
                  f"{pr['title'][:70]!r}")
            print(f"      -> #{later['number']} after {gap:.1f}h "
                  f"{later['title'][:70]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
