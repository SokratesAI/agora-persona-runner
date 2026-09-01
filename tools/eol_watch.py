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

**It also reads the live cluster, because a `FROM` line is not the only
place a support window is pinned.** Idea #178 is that both version
instruments here read Dockerfiles in our own repos, so software that runs
on this cluster without being built here has never been judged by either
of them. `tools.running_images` already reads every workload's image off
the API server; this takes the references it classes as *version*-pinned
and judges them exactly like a `FROM` line. The finding on the first run
is `couchdb:3.3`, which holds the owner's vault and Nova's own database
and went out of support 484 days ago -- visible to nothing here until
now. The other two classes are deliberately left alone: a digest names no
version, and a mutable tag such as `:latest` is `running_images`' own
finding rather than a support-window question.

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
from tools.running_images import classify, normalise, read_workloads, split_ref
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

# The leading `24` of `24-alpine`, the `3.12` of `3.12-slim`, the `3.3`
# of `v3.3.2`. Anything that does not start with a digit -- `latest`,
# `stable`, `bookworm` -- names no version line at all.
#
# The optional `v` is what Kubernetes images are tagged with far more
# often than Dockerfile base images are: `argocd:v3.3.2`,
# `tailscale:v1.102.3`, `dex:v2.43.0`. Without it every one of those read
# as "names no version" the moment this learned to read the cluster. The
# capture group deliberately excludes the `v`, because the catalogue's
# release names carry no prefix -- and `judge` therefore has to measure
# the variant from the match's own end rather than from the length of the
# version it captured, or `v3.3.2` would report a variant of `2`.
LEADING_VERSION_RE = re.compile(r"\Av?(\d+(?:\.\d+)*)")

# `  go-version: "1.25"` in a workflow step. A runtime is pinned in two
# shapes in this org and this reads the second: a `FROM` line pins what
# the image is built on, and a `setup-*` action pins what CI builds and
# tests with. They are the same question and drift apart independently --
# `SokratesAI/operator` pinned Go 1.25 in one Dockerfile and three
# workflow steps, and only the Dockerfile was ever read.
#
# The key's own prefix is the lookup: `go-version` -> `go`, `node-version`
# -> `node`, and both already resolve in the catalogue map above, so this
# needs no table of which action means which runtime -- which is the rule
# the module docstring states about the image map.
#
# **Resolving in the catalogue is not enough on its own, and that was my
# reviewer's finding on this change.** The catalogue is 300-odd products
# and its short names collide with ordinary workflow keys: `app` resolves
# to istio, `vault` to hashicorp-vault, `server` and `base` are claimed by
# two products each. So `app-version: "1.28.0"` in a release job -- which
# is about nothing at all -- resolved to Istio 1.28, which really is past
# its end of life, and printed a fabricated finding with a real product
# name and a real date on it. The narrowing is read out of the same file:
# a `<x>-version:` key counts only when that file also runs a
# `setup-<x>` action, which is the workflow itself declaring which
# runtimes it installs. Still no table -- the file is the source.
#
# `go-version-file: go.mod` deliberately does not match: the version is
# not written in this file, so there is nothing here to judge.
TOOLCHAIN_RE = re.compile(
    r"^\s*(?P<lang>[a-z][a-z0-9]*)-version:\s*"
    r"[\"']?(?P<version>[0-9][^\"'\s#]*)",
    re.MULTILINE)

# `uses: actions/setup-go@v7`, `uses: ruby/setup-ruby@v1`. The owner is
# not matched, so a third-party setup action counts the same as GitHub's.
SETUP_ACTION_RE = re.compile(r"uses:\s*\S*setup-([a-z][a-z0-9]*)@")

WORKFLOW_DIR = ".github/workflows/"

DEFAULT_WITHIN_DAYS = 180


def catalogue(opener=urllib.request.urlopen):
    """`(products, why)` -- every endoflife.date product with its releases."""
    try:
        with opener(CATALOGUE, timeout=45) as response:
            body = response.read()
    except Exception as exc:  # noqa: BLE001 -- any network shape is "unreadable"
        return None, f"could not reach {CATALOGUE}: {exc}"
    try:
        result = json.loads(body)["result"]
    except (ValueError, KeyError, TypeError) as exc:
        return None, f"{CATALOGUE} did not answer with a product list: {exc}"
    if not isinstance(result, list) or not result:
        return None, (f"{CATALOGUE} answered with no products, so there is "
                      "nothing to judge against")
    return result, None


def image_map(products):
    """`(mapping, ambiguous)` -- docker image name -> product, read off the API.

    Three sources, most specific first: the `pkg:docker/...` purls the
    catalogue publishes, then the product's own name, then its aliases.
    A later, weaker source never overrides a stronger one.

    **A name two products both claim maps to neither.** On the live
    catalogue `.../server` is claimed by both `couchbase-server` and
    `authentik`, and `.../base` by both `istio` and `discourse`, so a
    first-wins map answers `authentik` for an image called `server`
    purely because of the order the API returned its products in -- and
    would silently answer differently the day that order changes. Those
    names come back in `ambiguous` instead, and `judge` declines them by
    name rather than guessing. This was my reviewer's finding on
    runner#506 and I had written `setdefault` precisely because it looked
    like the careful choice.
    """
    claims = {}
    for rank, source in enumerate(("purl", "name", "alias")):
        for product in products:
            if source == "purl":
                names = [(i.get("id") or "").rsplit("/", 1)[-1]
                         for i in product.get("identifiers") or []
                         if (i.get("id") or "").startswith("pkg:docker/")]
            elif source == "name":
                names = [product["name"]]
            else:
                names = list(product.get("aliases") or [])
            for name in names:
                if not name:
                    continue
                held = claims.get(name)
                if held is None or held[0] > rank:
                    claims[name] = (rank, {product["name"]})
                elif held[0] == rank:
                    held[1].add(product["name"])
    mapping = {n: sorted(p)[0] for n, (_, p) in claims.items() if len(p) == 1}
    ambiguous = {n: sorted(p) for n, (_, p) in claims.items() if len(p) > 1}
    return mapping, ambiguous


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
        image, tag = match.group("image"), match.group("tag")
        # A stage reference never carries a tag, so requiring `tag is None`
        # stops a real image whose last path segment happens to equal a
        # stage alias from being dropped as one.
        if tag is None and image.lower() in stages:
            continue
        # What the regex stopped at tells the three untagged forms apart.
        # `FROM node:${NODE_VERSION}` stops at the `:` because `${` is not a
        # tag, so the marker is on the rest of the line, not in the match.
        rest = text[match.end():].split("\n", 1)[0]
        found.append({"repo": repo, "path": path, "image": image, "tag": tag,
                      "digest": tag is None and rest.startswith("@"),
                      "templated": tag is None and "$" in rest})
    return found


def toolchain_pins(repo, path, text):
    """Every `<runtime>-version:` pin in one workflow file, as dicts.

    Shaped exactly like `base_images` so `judge` needs no second branch:
    Only a key whose runtime this file also installs with a `setup-*`
    action is read. Without that the catalogue's own short names collide
    with ordinary workflow keys and the tool invents findings -- see the
    comment on `TOOLCHAIN_RE`.

    `image` carries the runtime token the catalogue map is keyed by, and
    `tag` carries the version. `kind` is what the report reads to say
    where the pin lives, since `go-version: 1.25` is not a `FROM` line
    and printing it as one would be a lie about a real place in a file.
    """
    installs = set(SETUP_ACTION_RE.findall(text))
    found = []
    for match in TOOLCHAIN_RE.finditer(text):
        lang = match.group("lang")
        if lang not in installs:
            continue
        found.append({"repo": repo, "path": path, "kind": "toolchain",
                      "image": lang, "tag": match.group("version"),
                      "digest": False, "templated": False})
    return found


def _release_for(product, version):
    """The catalogue release whose name is `version`, or the line above it.

    Two directions, and only one of them is an honest refusal.

    **Too coarse is ambiguous and is refused.** `python:3` sits above
    every 3.x line, so there is no single support window to report and a
    guess at which one it resolves to today would be an invention.

    **Too precise is not ambiguous and used to be refused anyway**, which
    was the reviewer's finding on runner#506 and would have made this
    tool quietly useless on the more careful pinning style. endoflife.date
    tracks each product at the vendor's own granularity -- Node by major,
    Python by minor -- so `node:20.11.0` and `python:3.12.7` matched no
    release name and fell out as NOT JUDGED, which never raises. A run
    could therefore exit 0 with a dead exact-pinned line in it: the exact
    "green while it sat there" failure this tool exists to end, one level
    down inside the tool. An exact version is a *refinement* of exactly
    one line, so it resolves to the longest release name that is a
    component-wise prefix of it. Component-wise matters: `3.1` is a
    string prefix of `3.12.7` and is a different Python.
    """
    releases = product.get("releases") or []
    for release in releases:
        if release.get("name") == version:
            return release
    parts = version.split(".")
    best = None
    for release in releases:
        name = release.get("name") or ""
        bits = name.split(".")
        if len(bits) < len(parts) and parts[:len(bits)] == bits:
            if best is None or len(bits) > len((best.get("name") or "").split(".")):
                best = release
    return best


def judge(image, products, mapping, today, within_days, ambiguous=None):
    """Fill one image dict with a verdict, or a reason it was not judged."""
    short = image["image"].rsplit("/", 1)[-1].lower()
    product_name = mapping.get(short)
    if product_name is None:
        claimed = (ambiguous or {}).get(short)
        if claimed:
            image["reason"] = ("endoflife.date has %d products claiming the "
                               "image name `%s` (%s), so which support "
                               "window this means is not decidable from the "
                               "catalogue" % (len(claimed), short,
                                              ", ".join(claimed)))
        else:
            image["reason"] = ("endoflife.date publishes no product for this "
                               "image, so it has no support window to read")
        return "not-judged"

    tag = image["tag"]
    if not tag:
        # A digest pin is the opposite of floating, and saying it follows
        # `latest` would be false about the most tightly pinned form there
        # is -- the reviewer's finding on runner#506. `FROM_RE`'s tag group
        # cannot contain `@` or `$`, so both land here with tag None and
        # only the source line can tell them apart.
        image["reason"] = (
            "pinned to a digest, which names no version line to look up — "
            "that is the hardened form, not drift" if image.get("digest")
            else "pinned through a build argument, so the line it resolves "
                 "to is not written in this file" if image.get("templated")
            else "no tag, so this follows `latest` and pins no line at all")
        return "not-judged"
    leading = LEADING_VERSION_RE.match(tag)
    if not leading:
        image["reason"] = (f"tag `{tag}` names no version, so there is no "
                           "support window to look up")
        return "not-judged"

    version = leading.group(1)
    image["product"], image["version"] = product_name, version
    rest = tag[leading.end():]
    if rest:
        image["variant"] = rest.lstrip("-")

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


def cluster_images(reader=read_workloads):
    """`(pins, problems)` -- every version-pinned image running on this cluster.

    Shaped exactly like `base_images` so `judge` and the report need no
    second branch, the same contract `toolchain_pins` keeps. `path`
    carries the namespace and the workload rather than a file, because
    that is the whole address a cluster reference has -- there is no line
    in a repo to send anyone to, and inventing one would be a lie about a
    real file.

    **Only the `version` class is taken, and the other two are not
    oversights.** A digest pins bytes and names no line to look up, which
    `judge` already says about a digest-pinned `FROM`; a mutable tag like
    `:latest` resolves to a different release on every pull, so the
    support window it sits in is not a property of anything written down
    -- that reference is `running_images`' finding, and answering it here
    with a window read off whatever the registry served this morning
    would be a fact with no source.
    """
    images, problems = reader()
    pins = []
    for image in images:
        ref = normalise(image["ref"])
        if classify(ref) != "version":
            continue
        name, tag, _ = split_ref(ref)
        pins.append({
            "repo": "live cluster",
            "path": "%s/%s %s" % (image["namespace"], image["kind"],
                                  image["name"]),
            "image": name,
            "tag": tag,
            "kind": "running",
        })
    return pins, problems


def sweep(repos, products, today, within_days, run=None):
    """`(judged, not_judged, problems)` across every repo given."""
    mapping, ambiguous = image_map(products)
    judged, not_judged, problems = [], [], []
    for repo in repos:
        paths, why = _tree(repo, run)
        if paths is None:
            problems.append(f"{repo}: could not list the repo — {why}")
            continue
        for path in paths:
            name = path.rsplit("/", 1)[-1]
            is_dockerfile = name.startswith("Dockerfile")
            is_workflow = (path.startswith(WORKFLOW_DIR)
                           and name.endswith((".yml", ".yaml")))
            if not (is_dockerfile or is_workflow):
                continue
            text, why = read_file(repo, path, run)
            if text is None:
                problems.append(f"{repo}: could not read {path} — {why}")
                continue
            if is_dockerfile:
                pins = base_images(repo, path, text)
            else:
                # A `-version:` key whose prefix names no product is not a
                # runtime pin -- `api-version`, `schema-version` -- so it is
                # dropped rather than reported NOT JUDGED. That is the
                # opposite call to the one made on a `FROM` line, and the
                # difference is that every `FROM` line really is a base
                # image, so an unmappable one is a gap worth printing.
                pins = [p for p in toolchain_pins(repo, path, text)
                        if p["image"] in mapping or p["image"] in ambiguous]
            for image in pins:
                where = judge(image, products, mapping, today, within_days,
                              ambiguous)
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
        key = (image.get("kind", "image"), image["image"], image["tag"])
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


def _pin(image):
    """How one pin is written where it lives.

    A `FROM` line and a `setup-*` step pin the same runtime in two
    different notations, and printing a workflow step as `go:1.25` would
    name a line that appears nowhere in the file the report sends you to.
    """
    if image.get("kind") == "toolchain":
        return "%s-version: %s" % (image["image"], image["tag"])
    return "%s:%s" % (image["image"], image["tag"] or "")


def _variant(image):
    variant = image.get("variant")
    return (f" — the `{variant}` half of the tag is a second line this "
            "cannot read") if variant else ""


def format_report(judged, not_judged, problems, notes, within_days):
    out = []
    bad = group([i for i in judged if i["verdict"] in ("eol", "soon")])
    if bad:
        out.append("RUNTIME SUPPORT — %d line(s) are out of support or "
                   "close to it." % len(bad))
        for members in sorted(bad.values(), key=lambda m: m[0]["days"]):
            image = members[0]
            when = ("ended %s, %d day(s) ago" % (image["eol"], -image["days"])
                    if image["verdict"] == "eol"
                    else "ends %s, in %d day(s)" % (image["eol"], image["days"]))
            out.append("  %s — %s security support %s%s"
                       % (_pin(image), image["product"], when,
                          _variant(image)))
            for place in _places(members):
                out.append("      %s" % place)
    ok = group([i for i in judged if i["verdict"] == "supported"])
    if ok:
        out.append("SUPPORTED — %d line(s), with the days each has left, "
                   "because the threshold decides the exit status and not "
                   "what you get to see:" % len(ok))
        for members in sorted(ok.values(), key=lambda m: m[0]["days"]):
            image = members[0]
            out.append("  %s — %s until %s, %d day(s) left%s"
                       % (_pin(image), image["product"], image["eol"],
                          image["days"], _variant(image)))
            out.append("      %s" % ", ".join(_places(members)))
    for members in sorted(group(not_judged).values(),
                          key=lambda m: (m[0].get("kind", "image"),
                                         m[0]["image"], m[0]["tag"] or "")):
        image = members[0]
        out.append("NOT JUDGED  %s — %s" % (_pin(image), image["reason"]))
        out.append("      %s" % ", ".join(_places(members)))
    for problem in problems:
        out.append("PROBLEM  %s" % problem)
    out.extend(notes)
    steps = sum(1 for i in judged if i.get("kind") == "toolchain")
    running = sum(1 for i in judged if i.get("kind") == "running")
    froms = len(judged) - steps - running
    out.append("Judged %d distinct runtime line(s) across %d FROM line(s), %d "
               "workflow version pin(s) and %d running container image(s) "
               "against endoflife.date, notice window %d day(s); %d not "
               "judged. A tag variant such as `-alpine` or `-slim` is a "
               "second line this cannot read and is never judged."
               % (len(group(judged)), froms, steps, running, within_days,
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

    today = dt.date.today()
    judged, not_judged, problems = sweep(
        repos, products, today, args.within_days)

    # The cluster is a source of pins, not a second sweep, so its findings
    # go through the same `judge` and land in the same two lists -- a
    # dead line is a dead line whether a Dockerfile or an API server
    # names it.
    mapping, ambiguous = image_map(products)
    pins, cluster_problems = cluster_images()
    problems.extend(cluster_problems)
    for image in pins:
        where = judge(image, products, mapping, today, args.within_days,
                      ambiguous)
        (judged if where == "judged" else not_judged).append(image)

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
        print("No runtime line was judged at all, which is no instrument "
              "rather than no finding.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
