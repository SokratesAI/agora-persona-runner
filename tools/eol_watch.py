"""Is every base image line this org builds on still getting security fixes?

Cycle 603, on the owner's idea #151 -- *"'Up to date' and 'still
supported' are different questions, and #141 only asks the first."*

`tools.pin_drift` answers the first question and says so every run: a
base image written `FROM python:3.12-slim` pins a **line**, not a
release, so there is no newer 3.12 to be behind and a version-gap check
reports it healthy forever. The row that filed this carries the proof --
`20.20.2` is the newest Node 20 that will ever exist, so the gap check
calls it current while the line itself stopped receiving security fixes.
That fact sat unread for four months while every check here said green.

    python3 -m tools.eol_watch

**The image-to-product map is read off the API, never typed here.**
`endoflife.date`'s v1 catalogue publishes a `purl` identifier per product
(`pkg:docker/library/node` -> `nodejs`) plus each product's aliases, and
this builds the map from those on every run. A hand-written table of
"which image is which product" is a second copy of the truth that goes
stale exactly the way the pin it watches does -- which is `pin_drift`'s
own rule about never reading a pinned value from a table in the tool.
One request, 2.7MB, about half a second: it carries every product's
release schedule too, so nothing needs a second call per image.

**It reads the org, not the workspace**, via `pin_drift`'s repo sweep, so
it cannot inherit `security_alerts`' old blind spot separately.

**A tag variant is not judged and the report says so.** `node:24-alpine`
pins two lines: Node 24, which is judged, and whatever Alpine the tag
happens to resolve to today, which is not written down anywhere in the
file and therefore is not a pin this can read. Judging the leading
version and staying quiet about the rest would report a partial answer as
a whole one.

**An image `endoflife.date` has no product for prints under NOT JUDGED
and does not raise.** That is the same call `pin_drift` makes on a commit
SHA and `security_alerts` makes on an already-fixed advisory: a check
that is red on day one and forever is the same as a check that is off.
The count is printed rather than dropped, so "nothing to act on" can
never be confused with "nothing was looked at".

**The 180-day notice window is chosen, not measured, and this says so.**
A runtime major migration here is a Dockerfile bump, a CI build and a
deploy, and CI in this org has been blocked for a week at a stretch, so
the window wants to be months rather than weeks. I have no measurement of
how long our own base-image migrations take -- there has never been one
to time. `--within-days` changes it, and the days remaining are printed
for every judged image whether or not they cross the line, so the
threshold decides the exit status and never what you get to see.

Exit status, matching `tools.pin_drift` and `tools.security_alerts`: 2
when a line is past its end-of-life or inside the notice window, 1 when
something was unreadable (which never reads as clean), 0 when every line
judged is supported.
"""

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request

from tools.pin_drift import _tree, read_file
from tools.security_alerts import _repos_to_sweep

CATALOGUE = "https://endoflife.date/api/v1/products/full"

# `FROM [--platform=...] <image>[:<tag>] [AS <stage>]`. The flag group is
# not decoration: `FROM --platform=$BUILDPLATFORM node:24-alpine` is the
# ordinary cross-build form, and a pattern that only refuses to read the
# flag as the image reads nothing at all on those lines. A digest-pinned
# or `$ARG`-templated image carries no line to look up and falls out
# rather than being guessed at.
FROM_RE = re.compile(
    r"^\s*FROM\s+(?:--\S+\s+)*(?P<image>[A-Za-z0-9][\w./-]*)"
    r"(?::(?P<tag>[\w.-]+))?",
    re.MULTILINE | re.IGNORECASE,
)

# The leading `24` of `24-alpine`, the `3.12` of `3.12-slim`. Anything
# that does not start with a digit -- `latest`, `stable`, `bookworm` --
# names no version line at all.
LEADING_VERSION_RE = re.compile(r"\A(\d+(?:\.\d+)*)")

DEFAULT_WITHIN_DAYS = 180


def catalogue(opener=urllib.request.urlopen):
    """`(products, why)` -- every endoflife.date product with its releases."""
    try:
        with opener(CATALOGUE, timeout=45) as response:
            body = response.read()
    except Exception as exc:  # noqa: BLE001 -- any network shape is "unreadable"
        return None, f"could not reach {CATALOGUE}: {exc}"
    try:
        return json.loads(body)["result"], None
    except (ValueError, KeyError, TypeError) as exc:
        return None, f"{CATALOGUE} did not answer with a product list: {exc}"


def image_map(products):
    """Docker image name -> endoflife.date product name, read off the API.

    Three sources, most specific first: the `pkg:docker/...` purls the
    catalogue publishes, then the product's own name, then its aliases.
    `setdefault` keeps the first, so a purl always beats an alias.
    """
    mapping = {}
    for product in products:
        for identifier in product.get("identifiers") or []:
            ident = identifier.get("id") or ""
            if ident.startswith("pkg:docker/"):
                mapping.setdefault(ident.rsplit("/", 1)[-1], product["name"])
    for product in products:
        mapping.setdefault(product["name"], product["name"])
        for alias in product.get("aliases") or []:
            mapping.setdefault(alias, product["name"])
    return mapping


def base_images(repo, path, text):
    """Every `FROM` line in one Dockerfile, as dicts.

    A stage name (`FROM builder`) is a reference to an earlier `FROM` in
    the same file, not an image, so it is dropped here rather than
    reported as unmappable.
    """
    stages = {
        match.lower()
        for match in re.findall(r"^\s*FROM\s+(?:--\S+\s+)*\S+\s+AS\s+(\S+)",
                                text, re.MULTILINE | re.IGNORECASE)
    }
    found = []
    for match in FROM_RE.finditer(text):
        image = match.group("image")
        if image.lower() in stages:
            continue
        found.append({"repo": repo, "path": path, "image": image,
                      "tag": match.group("tag")})
    return found


def _release_for(product, version):
    """The catalogue release whose name is `version`, or the closest parent.

    `python:3.12` names a release directly; `python:3` does not, and the
    honest answer there is that the tag pins no single support window
    rather than a guess at which 3.x it resolves to today.
    """
    for release in product.get("releases") or []:
        if release.get("name") == version:
            return release
    return None


def judge(image, products, mapping, today, within_days):
    """Fill one image dict with a verdict, or a reason it was not judged."""
    product_name = mapping.get(image["image"].rsplit("/", 1)[-1].lower())
    if product_name is None:
        image["reason"] = ("endoflife.date publishes no product for this "
                           "image, so it has no support window to read")
        return "not-judged"

    tag = image["tag"]
    if not tag:
        image["reason"] = ("no tag, so this follows `latest` and pins no "
                           "line at all")
        return "not-judged"
    leading = LEADING_VERSION_RE.match(tag)
    if not leading:
        image["reason"] = (f"tag `{tag}` names no version, so there is no "
                           "support window to look up")
        return "not-judged"

    version = leading.group(1)
    image["product"], image["version"] = product_name, version
    if tag != version:
        image["variant"] = tag[len(version):].lstrip("-")

    product = next((p for p in products if p["name"] == product_name), None)
    release = _release_for(product or {}, version)
    if release is None:
        image["reason"] = (f"{product_name} publishes no release line named "
                           f"`{version}`, so this tag pins no single support "
                           "window")
        return "not-judged"

    eol = release.get("eolFrom")
    image["eol"] = eol
    if release.get("isEol"):
        image["verdict"] = "eol"
        image["days"] = _days(eol, today)
        return "judged"
    if eol is None:
        image["reason"] = (f"{product_name} {version} has no end-of-life date "
                           "published yet")
        return "not-judged"
    days = _days(eol, today)
    if days is None:
        image["reason"] = f"{product_name} {version} carries an unreadable "\
                          f"end-of-life date `{eol}`"
        return "not-judged"
    image["days"] = days
    image["verdict"] = "soon" if days <= within_days else "supported"
    return "judged"


def _days(eol, today):
    """Days from `today` to an `eolFrom` date; negative once it has passed."""
    try:
        return (dt.date.fromisoformat(eol) - today).days
    except (TypeError, ValueError):
        return None


def sweep(repos, products, today, within_days, run=None):
    """`(judged, not_judged, problems)` across every repo given."""
    mapping = image_map(products)
    judged, not_judged, problems = [], [], []
    for repo in repos:
        paths, why = _tree(repo, run)
        if paths is None:
            problems.append(f"{repo}: could not list the repo — {why}")
            continue
        for path in paths:
            if not path.rsplit("/", 1)[-1].startswith("Dockerfile"):
                continue
            text, why = read_file(repo, path, run)
            if text is None:
                problems.append(f"{repo}: could not read {path} — {why}")
                continue
            for image in base_images(repo, path, text):
                where = judge(image, products, mapping, today, within_days)
                (judged if where == "judged" else not_judged).append(image)
    return judged, not_judged, problems


def group(images):
    """Collapse identical `image:tag` pins into one entry with its places.

    A multi-stage Dockerfile names the same base image in every stage, so
    `whatsapp-bridge` reported `node:20-alpine` twice for one decision.
    One question, reported once -- `pin_drift._group`'s rule, and the
    places are all still printed underneath.
    """
    groups = {}
    for image in images:
        key = (image["image"], image["tag"])
        groups.setdefault(key, []).append(image)
    return groups


def _places(members):
    """Every repo and path one grouped pin was found at, deduplicated."""
    seen, out = set(), []
    for member in members:
        where = f"{member['repo']}  {member['path']}"
        if where not in seen:
            seen.add(where)
            out.append(where)
    return out


def _variant(image):
    variant = image.get("variant")
    return (f" — the `{variant}` half of the tag is a second line this "
            "cannot read") if variant else ""


def format_report(judged, not_judged, problems, notes, within_days):
    out = []
    bad = group([i for i in judged if i["verdict"] in ("eol", "soon")])
    if bad:
        out.append("BASE IMAGE SUPPORT — %d line(s) are out of support or "
                   "close to it." % len(bad))
        for members in sorted(bad.values(), key=lambda m: m[0]["days"]):
            image = members[0]
            when = ("ended %s, %d day(s) ago" % (image["eol"], -image["days"])
                    if image["verdict"] == "eol"
                    else "ends %s, in %d day(s)" % (image["eol"], image["days"]))
            out.append("  %s:%s — %s security support %s%s"
                       % (image["image"], image["tag"], image["product"],
                          when, _variant(image)))
            for place in _places(members):
                out.append("      %s" % place)
    ok = group([i for i in judged if i["verdict"] == "supported"])
    if ok:
        out.append("SUPPORTED — %d line(s), with the days each has left, "
                   "because the threshold decides the exit status and not "
                   "what you get to see:" % len(ok))
        for members in sorted(ok.values(), key=lambda m: m[0]["days"]):
            image = members[0]
            out.append("  %s:%s — %s until %s, %d day(s) left%s"
                       % (image["image"], image["tag"], image["product"],
                          image["eol"], image["days"], _variant(image)))
            out.append("      %s" % ", ".join(_places(members)))
    for members in sorted(group(not_judged).values(),
                          key=lambda m: (m[0]["image"], m[0]["tag"] or "")):
        image = members[0]
        out.append("NOT JUDGED  %s%s — %s"
                   % (image["image"], f":{image['tag']}" if image["tag"] else "",
                      image["reason"]))
        out.append("      %s" % ", ".join(_places(members)))
    for problem in problems:
        out.append("PROBLEM  %s" % problem)
    out.extend(notes)
    out.append("Judged %d distinct base image line(s) across %d FROM line(s) "
               "against endoflife.date, notice window %d day(s); %d not "
               "judged. A tag variant such as `-alpine` or `-slim` is a "
               "second line this cannot read and is never judged."
               % (len(group(judged)), len(judged), within_days,
                  len(group(not_judged))))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", action="append",
                        help="owner/name; repeatable. Defaults to every "
                             "non-archived repo in every org this workspace "
                             "names, plus the checkouts here.")
    parser.add_argument("--within-days", type=int, default=DEFAULT_WITHIN_DAYS,
                        help="raise on a line whose support ends inside this "
                             "many days (default %d)" % DEFAULT_WITHIN_DAYS)
    args = parser.parse_args(argv)

    if args.repo:
        repos, unplaceable, notes, incomplete = sorted(args.repo), [], [], False
    else:
        repos, unplaceable, notes, incomplete = _repos_to_sweep()
    for clone in unplaceable:
        notes.append(f"⚠ a checkout at {clone} names no GitHub remote this "
                     "could place, so it was not swept.")

    products, why = catalogue()
    if products is None:
        print("PROBLEM  %s" % why)
        print("The support-window catalogue was unreadable, so nothing was "
              "judged. That is no instrument, not no finding.")
        return 1

    judged, not_judged, problems = sweep(
        repos, products, dt.date.today(), args.within_days)
    print(format_report(judged, not_judged, problems, notes, args.within_days))

    unreadable = bool(problems or incomplete or unplaceable)
    if unreadable:
        print("Something here was unreadable, so this run cannot claim the "
              "sweep was complete.")
    # A support finding outranks an incomplete sweep, the same call
    # `pin_drift` makes: both are true, only one is actionable, and the
    # sibling contract is that 2 means "go and do something".
    if any(i["verdict"] in ("eol", "soon") for i in judged):
        return 2
    if unreadable:
        return 1
    if not judged:
        print("No base image line was judged at all, which is no instrument "
              "rather than no finding.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
