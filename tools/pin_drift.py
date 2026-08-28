"""Has any pinned tool version in this org fallen behind upstream?

Cycle 561, on the owner's idea #141 -- *"a check that compares a pinned
version in a Dockerfile against the latest published release, and says
something once the gap passes some number of releases, would catch the
next one in days instead of weeks. It generalises past the CLI to any
version we pin."*

`tools.cli_pin` already asks this question about exactly one pin, the
Claude Code CLI, because that is the one the loop executes inside. Every
other pin in this org had nothing reading it. The first run found
`actions/checkout@v4` against an upstream `v7.0.1` published five weeks
earlier, in twenty workflow files -- and the fact that v7 exists was
already written down in this loop's own research on the CI-builder idea,
five days before, having been read for a different reason and never
turned into a bump. That is `security_alerts`' occasionally-noticed fact
again, one dependency over.

    python3 -m tools.pin_drift

**It reads the org, not the workspace.** `security_alerts` swept only the
repos with a checkout here until Cycle 432 and reported the org clean
while `sokrates-docs` carried four high-severity alerts; the repo list
comes from that module's own `_repos_to_sweep` so this cannot inherit the
same blind spot separately.

**The pinned value is always read out of the file, never from a table
here.** A table of "what we pin" is a second copy of the truth and goes
stale exactly the way the pin it watches does -- which is the failure
this tool exists to report.

**Three things are excluded on purpose and the report names each one.**
The Claude Code pin belongs to `tools.cli_pin`, which knows things this
does not (the running binary, the stream-json contract). An action
pinned to a commit SHA carries no version at all -- that is the hardened
form, and the first run of this tool read `actions/checkout@3d3c42e5...`
as "major 3, four majors behind", which is a real measurement written up
as something it was not. And a base image written
`FROM python:3.12-slim` pins a *line*, not a release: there is no newer
3.12 to be behind, so a version-gap check reports it healthy forever
while the real question is whether that line still gets security fixes.
That is a different question with a different source, and it is already
boarded as idea #151.

**It reads the Crossplane composition that creates a repo, as well as
the repos.** `platform-config/crossplane/githubservice-composition.yaml`
holds the whole of `.github/workflows/build.yaml` as one escaped string,
so it is neither in `.github/workflows/` nor on lines this tool could
match, and it read as carrying no pins at all -- while being the file
that stamps CI into every repo the platform creates. Cycle 587 found it
six versions behind by an accidental grep, bumped it, and filed the
blindness; Cycle 588 is this. Reading every `crossplane/*.ya?ml` costs
about 11 seconds on `platform-config` (4.3s to 15.7s, measured), which
is the price of not finding the next one by accident.

**`KUBECTL_VERSION` is judged against the cluster, not against upstream.**
Kubernetes supports `kubectl` within one minor of `kube-apiserver`, so the
newest kubectl that exists is the wrong ceiling for a pin the loop actually
runs against this cluster. Until Cycle 589 this compared it to
`dl.k8s.io/release/stable.txt`, and on 2026-08-28 it reported `v1.36.2` as
"a minor behind v1.37.0" and asked for a bump -- against an API server at
`v1.34.4+k3s1`, where kubectl already prints *"version difference between
client (1.36) and server (1.34) exceeds the supported minor version skew of
+/-1"* on every single invocation. The check was reading a real number and
pointing it the wrong way. The ceiling is now `stable-<major>.<server minor
+ 1>.txt`, read after one `kubectl version -o json`; a cluster this cannot
read is a problem line and exit 1, never a clean answer.

**A patch gap is not a finding.** Semver makes a patch release
backwards-compatible and upstreams publish them continuously, so "behind
by a patch" fires on almost every run and a check that always fires is
one nobody reads -- `cli_pin`'s own reasoning for its staleness window.
A minor or major gap is somebody having decided, implicitly, not to take
a change. That is the thing worth a line.

Exit status, matching `tools.security_alerts` and `tools.cli_pin`: 2 when
a pin is behind by a minor or a major, 1 when something was unreadable
(which never reads as clean), 0 when every pin swept is current or behind
only by a patch.
"""

import argparse
import base64
import json
import re
import sys
import urllib.request

import subprocess

from tools.security_alerts import _gh, _repos_to_sweep


def _kubectl(args):
    """Run `kubectl` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(
        ["kubectl"] + args, capture_output=True, text=True, timeout=60
    )
    return proc.returncode, proc.stdout, proc.stderr

# `ARG NAME_VERSION=value` in a Dockerfile. The name is the key into
# UPSTREAM below; a pin whose name is not there is reported as unmatched
# rather than skipped, so a new pin cannot be silently uncovered.
DOCKER_PIN_RE = re.compile(r"^\s*ARG\s+([A-Z0-9_]+_VERSION)\s*=\s*(\S+)", re.MULTILINE)
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)@(v?[0-9][\w.-]*)",
                     re.MULTILINE)

# The same pin, written inside a quoted string rather than on a line of
# its own. `platform-config/crossplane/githubservice-composition.yaml`
# carries the whole of `.github/workflows/build.yaml` as one escaped
# double-quoted scalar, so every `uses:` in it sits behind a literal
# backslash-n and `^` never reaches it -- this tool read that file's
# pins as absent for its whole life. Cycle 587 found the file was six
# versions behind by an accidental grep while sweeping eleven repos by
# hand, bumped it, and filed the blindness rather than the pin. This is
# what watches it now, and it matters more than an ordinary workflow
# does: the composition is what stamps CI into every repo the platform
# creates, so a stale pin here is not one repo behind, it is every
# future repo born behind.
#
# Matching the escape is deliberate rather than parsing the YAML and
# re-parsing each string value as YAML: the embedded document is full of
# `${{ }}` and is not this tool's to understand. It needs the version,
# not the structure.
EMBEDDED_USES_RE = re.compile(
    r"\\n\s*-?\s*uses:\s*([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)@(v?[0-9][\w.-]*)"
)

# Both are action pins and resolve the same way; the kind is kept apart
# only so the report can say where the pin actually lands.
ACTION_KINDS = ("action", "template-action")

K8S_STABLE = "https://dl.k8s.io/release/stable.txt"
K8S_STABLE_MINOR = "https://dl.k8s.io/release/stable-%d.%d.txt"

# Said by two paths and matched on by the caller, so it lives here rather
# than being written out twice and drifting.
NO_RELEASES = "%s publishes no releases, so there is no upstream version "\
              "to compare against"

# Dockerfile ARG name -> where its upstream release lives.
#   ("k8s", None)          the newest kubectl this cluster's API server
#                          supports -- not the newest kubectl there is
#   ("release", "o/r")     the latest GitHub release of that repo
UPSTREAM = {
    "KUBECTL_VERSION": ("k8s", None),
    "GH_CLI_VERSION": ("release", "cli/cli"),
    "SOKRATES_VERSION": ("release", "SokratesAI/sokrates-cli"),
}

# Pins this deliberately does not judge, and who does.
EXCLUDED = {
    "CLAUDE_CODE_VERSION": "tools.cli_pin owns it — it also reads the running "
                           "binary and the stream-json contract",
}


# A commit SHA is the hardened way to pin an action and it carries no
# version at all. The first run of this tool read
# `actions/checkout@3d3c42e5...` as major 3 against upstream v7 and
# printed "4 majors behind" for a pin that is not behind anything -- a
# real measurement written up as something it was not. Anything that is
# all hex and long enough to be a commit is reported, never compared.
SHA_RE = re.compile(r"\A[0-9a-f]{7,40}\Z")


def is_sha(ref):
    r"""Is this ref a commit pin rather than a version?

    `\d`-only refs are excluded from the hex test on purpose: `v4` and
    `40` are both legal tags and neither is a commit.
    """
    text = str(ref or "")
    return bool(SHA_RE.fullmatch(text)) and not text.isdigit()


def version_parts(text):
    """`(major, minor, patch)` from a version string, or None.

    Leading `v` is stripped and a missing component reads as absent, not
    as zero: `v4` on an action tag means "the v4 line", and pretending it
    is 4.0.0 would invent a minor gap against `v4.2.1`.
    """
    match = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text or ""))
    if not match:
        return None
    return tuple(int(p) if p is not None else None for p in match.groups())


def gap(pinned, latest):
    """`"major"`, `"minor"`, `"patch"`, `"ahead"`, `"current"`, or None.

    Compared only as deep as the *pin* is written. `actions/checkout@v4`
    says nothing about a minor, so against `v7.0.1` the answer is a major
    gap and against `v4.9.0` it is `current` -- the floating tag really
    does move within its major, so there is nothing there to bump.

    `"ahead"` is a pin *past* what it is compared against, and it read as
    `current` until Cycle 589. That was harmless while every comparison was
    against an upstream latest, where being ahead is nearly impossible. It
    stopped being harmless once `KUBECTL_VERSION` started being judged
    against a ceiling the cluster sets: `v1.36.2` against a supported
    `v1.35.x` is the actual defect, and reporting it as `current` is the
    same silence the check exists to break.
    """
    low, high = version_parts(pinned), version_parts(latest)
    if low is None or high is None:
        return None
    for name, index in (("major", 0), ("minor", 1), ("patch", 2)):
        if low[index] is None:
            return "current"
        if high[index] is None:
            return "current"
        if low[index] < high[index]:
            return name
        if low[index] > high[index]:
            return "ahead"
    return "current"


def _tree(repo, run=None):
    """Every path on a repo's default branch, or (None, why)."""
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/git/trees/HEAD?recursive=1", "--jq", ".tree[].path"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    return [line for line in out.splitlines() if line.strip()], None


def interesting_paths(paths):
    """The files in a tree that can carry a pin this tool understands."""
    out = []
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        if name.startswith("Dockerfile"):
            out.append(path)
        elif path.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
            out.append(path)
        elif path.startswith("crossplane/") and name.endswith((".yml", ".yaml")):
            out.append(path)
    return sorted(out)


def read_file(repo, path, run=None):
    """A file's text off the default branch, or (None, why)."""
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/contents/{path}", "--jq", ".content"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        return base64.b64decode(out).decode("utf-8"), None
    except (ValueError, UnicodeDecodeError) as exc:
        return None, f"could not decode {path}: {exc}"


def pins_in(repo, path, text):
    """Every pin this tool can judge in one file, as dicts."""
    found = []
    name = path.rsplit("/", 1)[-1]
    if name.startswith("Dockerfile"):
        for arg, value in DOCKER_PIN_RE.findall(text):
            found.append({"repo": repo, "path": path, "what": arg,
                          "pinned": value, "kind": "docker-arg"})
    else:
        for action, ref in USES_RE.findall(text):
            found.append({"repo": repo, "path": path, "what": action,
                          "pinned": ref, "kind": "action"})
        seen = {(p["what"], p["pinned"]) for p in found}
        for action, ref in EMBEDDED_USES_RE.findall(text):
            # A file can hold the same pin both ways -- a composition
            # that pins an action for itself and again inside the
            # workflow it writes. One question, reported once.
            if (action, ref) in seen:
                continue
            seen.add((action, ref))
            found.append({"repo": repo, "path": path, "what": action,
                          "pinned": ref, "kind": "template-action"})
    return found


def _read(url, opener):
    try:
        with opener(url, timeout=30) as response:
            return response.read().decode("utf-8").strip(), None
    except Exception as exc:  # noqa: BLE001 -- any network shape is "unreadable"
        return None, f"could not reach {url}: {exc}"


def server_minor(run=None):
    """`((major, minor), gitVersion, why)` for the API server this pod talks to.

    `minor` comes back as `"34"` on upstream and `"34+"` on some
    distributions, so it is read with a regex rather than `int()`.
    """
    code, out, err = (run or _kubectl)(["version", "-o", "json"])
    if code != 0:
        blob = (err or out or "").strip()
        return None, None, blob.splitlines()[0] if blob else f"kubectl exited {code}"
    try:
        server = json.loads(out)["serverVersion"]
        major = int(re.match(r"\d+", str(server["major"])).group())
        minor = int(re.match(r"\d+", str(server["minor"])).group())
    except Exception as exc:  # noqa: BLE001 -- any shape here is unreadable
        return None, None, f"could not read serverVersion out of kubectl: {exc}"
    return (major, minor), server.get("gitVersion", f"v{major}.{minor}"), None


def latest_k8s(opener=urllib.request.urlopen, run=None):
    """The newest kubectl release this cluster's API server supports.

    **Not the newest kubectl that exists**, which is what this asked until
    Cycle 589 and is the wrong question. Kubernetes supports kubectl within
    one minor of `kube-apiserver` in either direction. This cluster answers
    `v1.34.4+k3s1`; the pin was `v1.36.2` and the upstream stable channel
    said `v1.37.0`, so the check was reporting "a minor behind" and asking
    for a bump that widens a skew kubectl itself already warns about on
    every invocation: *"version difference between client (1.36) and server
    (1.34) exceeds the supported minor version skew of +/-1"*. The ceiling
    is therefore server-minor plus one, read from that minor's own stable
    channel; if that minor has not been published yet, the server's own.
    """
    where, git_version, why = server_minor(run)
    if where is None:
        return None, f"could not read the cluster's API server version -- {why}"
    major, minor = where
    for candidate in (minor + 1, minor):
        latest, read_why = _read(K8S_STABLE_MINOR % (major, candidate), opener)
        if latest:
            return latest, None
    return None, f"could not reach {K8S_STABLE_MINOR % (major, minor + 1)} -- {read_why}"


def latest_release(repo, run=None):
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/releases/latest", "--jq", ".tag_name"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        # A repo with no published release answers 404 here. That is a
        # fact about the repo, not a failed read, and treating it as one
        # would leave this tool red forever over a pin nobody can judge.
        if "404" in blob or "Not Found" in blob:
            return None, NO_RELEASES % repo
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    tag = out.strip()
    return (tag, None) if tag else (None, NO_RELEASES % repo)


def resolve(pin, cache, run=None, opener=urllib.request.urlopen):
    """`(latest, source, why-not)` for one pin, memoised across repos.

    Twenty files pinning `actions/checkout` are one upstream question, so
    the cache is keyed on the upstream rather than on the pin.
    """
    if pin["kind"] in ACTION_KINDS:
        key = ("release", pin["what"])
    else:
        target = UPSTREAM.get(pin["what"])
        if target is None:
            return None, None, "no upstream is configured for this ARG name"
        key = target
    if key not in cache:
        if key[0] == "k8s":
            cache[key] = latest_k8s(opener, run) + (
                "the newest minor this cluster's API server supports",)
        else:
            cache[key] = latest_release(key[1], run) + (f"{key[1]} releases",)
    latest, why, source = cache[key]
    return latest, source, why


def sweep(repos, run=None, opener=urllib.request.urlopen):
    """`(judged, excluded, problems)` across every repo given."""
    judged, excluded, problems = [], [], []
    cache = {}
    for repo in repos:
        paths, why = _tree(repo, run)
        if paths is None:
            problems.append(f"{repo}: could not list the repo — {why}")
            continue
        for path in interesting_paths(paths):
            text, why = read_file(repo, path, run)
            if text is None:
                problems.append(f"{repo}: could not read {path} — {why}")
                continue
            for pin in pins_in(repo, path, text):
                if pin["what"] in EXCLUDED:
                    pin["reason"] = EXCLUDED[pin["what"]]
                    excluded.append(pin)
                    continue
                if is_sha(pin["pinned"]):
                    pin["reason"] = ("pinned to a commit, which carries no "
                                     "version to compare — that is the "
                                     "hardened form, not drift")
                    excluded.append(pin)
                    continue
                latest, source, why = resolve(pin, cache, run, opener)
                if latest is None:
                    if why and "publishes no releases" in why:
                        pin["reason"] = why
                        excluded.append(pin)
                        continue
                    problems.append(
                        f"{repo}: {path}: {pin['what']} pinned {pin['pinned']}, "
                        f"upstream unreadable — {why}"
                    )
                    continue
                pin["latest"], pin["source"] = latest, source
                pin["gap"] = gap(pin["pinned"], latest)
                if pin["gap"] is None:
                    problems.append(
                        f"{repo}: {path}: {pin['what']} pinned {pin['pinned']} "
                        f"against {latest} — neither reads as a version, so "
                        "this pin was not judged"
                    )
                    continue
                judged.append(pin)
    return judged, excluded, problems


def _group(pins):
    """Collapse identical (what, pinned, latest) triples across files."""
    groups = {}
    for pin in pins:
        key = (pin["what"], pin["pinned"], pin["latest"])
        groups.setdefault(key, []).append(pin)
    return groups


def format_report(judged, excluded, problems, notes):
    lines = []
    behind = [p for p in judged if p["gap"] in ("major", "minor")]
    if behind:
        lines.append(
            f"PIN DRIFT — {len(_group(behind))} pinned version(s) are behind "
            "upstream by a minor or a major."
        )
        for (what, pinned, latest), pins in sorted(_group(behind).items()):
            severity = pins[0]["gap"]
            lines.append(f"  {what}: pinned {pinned}, upstream {latest} "
                         f"({severity} behind, per {pins[0]['source']})")
            places = {}
            for pin in pins:
                key = (pin["repo"], pin["path"],
                       pin.get("kind") == "template-action")
                places[key] = places.get(key, 0) + 1
            for (repo, path, is_template), count in sorted(places.items()):
                times = f"  ({count} uses)" if count > 1 else ""
                # Without this the line reads as one repo's own CI being
                # behind, which is the cheap reading and the wrong one.
                stamped = "  — a template, stamped into every repo it creates" \
                    if is_template else ""
                lines.append(f"      {repo}  {path}{times}{stamped}")
    ahead = [p for p in judged if p["gap"] == "ahead"]
    if ahead:
        lines.append(
            f"PINNED PAST WHAT IT IS JUDGED AGAINST — {len(_group(ahead))} "
            "pin(s) are ahead, which is drift in the other direction."
        )
        for (what, pinned, latest), pins in sorted(_group(ahead).items()):
            lines.append(f"  {what}: pinned {pinned}, ceiling {latest} "
                         f"(ahead, per {pins[0]['source']})")
            for repo, path in sorted({(p["repo"], p["path"]) for p in pins}):
                lines.append(f"      {repo}  {path}")
    patch = [p for p in judged if p["gap"] == "patch"]
    if patch:
        lines.append("Behind by a patch only, which is not a finding — semver "
                     "makes these backwards-compatible and they publish "
                     "continuously:")
        for (what, pinned, latest), pins in sorted(_group(patch).items()):
            lines.append(f"  {what}: pinned {pinned}, upstream {latest} "
                         f"({len(pins)} file(s))")
    current = [p for p in judged if p["gap"] == "current"]
    lines.append(
        f"Judged {len(judged)} pin(s): {len(behind)} behind, {len(ahead)} "
        f"ahead, {len(patch)} patch-only, {len(current)} current."
    )
    skipped = {}
    for pin in excluded:
        key = (pin["what"], pin["pinned"], pin["reason"])
        skipped.setdefault(key, set()).add((pin["repo"], pin["path"]))
    for (what, pinned, reason), places in sorted(skipped.items()):
        where = ", ".join(f"{r} {p}" for r, p in sorted(places))
        lines.append(f"NOT JUDGED  {what} = {pinned} ({where}) — {reason}")
    lines.append("A `FROM image:tag` base image is not judged either: it pins a "
                 "line, so there is no upstream gap to find and the real "
                 "question is whether the line is still supported — idea #151.")
    for note in notes:
        lines.append(note)
    for problem in problems:
        lines.append(f"⚠ {problem}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", action="append",
                        help="owner/name; repeatable. Defaults to every "
                             "non-archived repo in every org this workspace "
                             "names, plus the checkouts here.")
    args = parser.parse_args(argv)

    if args.repo:
        repos, unplaceable, notes, incomplete = sorted(args.repo), [], [], False
    else:
        repos, unplaceable, notes, incomplete = _repos_to_sweep()
    for clone in unplaceable:
        notes.append(f"⚠ a checkout at {clone} names no GitHub remote this "
                     "could place, so it was not swept.")

    judged, excluded, problems = sweep(repos)
    print(format_report(judged, excluded, problems, notes))

    unreadable = bool(problems or incomplete or unplaceable)
    if unreadable:
        print("Something here was unreadable, so this run cannot claim the "
              "sweep was complete.")
    # A drift finding outranks an incomplete sweep. Both are true and only
    # one of them is actionable, and `security_alerts`' contract is that 2
    # means "go and do something" -- returning 1 here would hide a real
    # bump behind a pin whose upstream nobody has configured yet.
    if any(p["gap"] in ("major", "minor", "ahead") for p in judged):
        return 2
    if unreadable:
        return 1
    if not judged:
        print("No pin was judged at all, which is no instrument rather than "
              "no drift.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
