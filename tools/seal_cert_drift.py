"""Is `platform-config` still sealing against the key the controller holds?

`secrets/seal-secrets.sh` in `SokratesAI/platform-config` encrypts every
platform credential with one committed file, `secrets/sealed-secrets-pub.pem`.
It runs in GitHub Actions, offline from this cluster, so nothing in that
pipeline ever compares its cert against the controller. When sealed-secrets
rotates -- which it does on its own, and did on 2026-09-03 20:03:10 GMT after
a pod restart -- the committed copy silently goes stale and every new seal is
encrypted to a retired key.

**A stale cert is not an outage, and that is exactly why nothing caught it.**
The controller retains old private keys, so existing SealedSecrets keep
unsealing and the cluster looks perfectly healthy; the March 2026 key was six
months out of date and every SealedSecret in the repo still reported
`unsealed successfully`. The damage is conditional: a controller reinstall, a
lost `kube-system` key Secret or a `--key-cutoff-time` retires the old key,
and at that moment everything sealed after the rotation becomes
undecryptable. There is no signal between the rotation and the disaster. This
check is that signal.

**It compares public keys, not certificates.** The property that decides
whether a seal can be opened is the RSA key inside the cert, so a cert
re-issued around the same key is not drift and must not raise. Both sides go
through `openssl x509 -pubkey -noout`, which is the same extraction `kubeseal`
performs, and the SPKI PEMs are compared byte for byte.

The committed side is read from GitHub rather than from a local checkout, on
purpose: a checkout can be behind `main` by hours, and "the cert this repo
would seal with" means the one on the default branch.

Exit 0 the two keys match, exit 2 they have drifted, exit 1 either side could
not be read. Unreadable never reads as clean.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request

CONTROLLER = "http://sealed-secrets.kube-system.svc.cluster.local:8080/v1/cert.pem"
REPO = "SokratesAI/platform-config"
CERT_PATH = "secrets/sealed-secrets-pub.pem"
TIMEOUT = 20


def fetch_live(url=CONTROLLER, open_url=None):
    """`(pem, None)` from the controller's own cert endpoint, or `(None, why)`."""
    opener = open_url or urllib.request.urlopen
    try:
        with opener(url, timeout=TIMEOUT) as response:
            return response.read().decode("utf-8"), None
    except Exception as exc:                        # noqa: BLE001 - any failure is "unreadable"
        return None, f"could not read the controller's cert at {url}: {exc}"


def fetch_committed(repo=REPO, path=CERT_PATH, run=None):
    """`(pem, None)` for the copy on the repo's default branch, or `(None, why)`.

    `run` is injected and resolved at call time rather than bound as a default
    argument -- a default binds the function object at import, so a test that
    patches `_gh` would leave this calling the real `gh` against the real repo.
    """
    code, out, err = (run or _gh)(["api", f"repos/{repo}/contents/{path}"])
    if code != 0:
        return None, f"could not read {repo} {path} from GitHub: {err.strip() or out.strip()}"
    try:
        payload = json.loads(out)
        return base64.b64decode(payload["content"]).decode("utf-8"), None
    except Exception as exc:                        # noqa: BLE001
        return None, f"could not decode {repo} {path}: {exc}"


def _gh(args):
    """Run `gh` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def _openssl(args, stdin):
    proc = subprocess.run(
        ["openssl"] + args, input=stdin, capture_output=True, text=True, timeout=30
    )
    return proc.returncode, proc.stdout, proc.stderr


def public_key(pem, run=None):
    """`(spki_pem, None)` — the SubjectPublicKeyInfo `kubeseal` would encrypt to."""
    code, out, err = (run or _openssl)(["x509", "-pubkey", "-noout"], pem)
    if code != 0 or "BEGIN PUBLIC KEY" not in out:
        return None, f"openssl could not read that certificate: {err.strip() or out.strip()}"
    return out.strip(), None


def describe(pem, run=None):
    """A one-line `notBefore=... serial=...`, or `''` when openssl cannot say."""
    code, out, _ = (run or _openssl)(["x509", "-noout", "-startdate", "-serial"], pem)
    if code != 0:
        return ""
    return " ".join(line.strip() for line in out.splitlines() if line.strip())


def report(live, committed, run=None):
    """`(exit_status, lines)` for one pair of PEMs, either of which may be an error."""
    lines = []
    live_pem, live_err = live
    committed_pem, committed_err = committed

    if live_err or committed_err:
        for err in (live_err, committed_err):
            if err:
                lines.append(err)
        lines.append(
            "Cannot say whether the seal cert has drifted. That is the finding — an "
            "unreadable side is not a clean one."
        )
        return 1, lines

    live_key, live_key_err = public_key(live_pem, run)
    committed_key, committed_key_err = public_key(committed_pem, run)
    if live_key_err or committed_key_err:
        for err in (live_key_err, committed_key_err):
            if err:
                lines.append(err)
        return 1, lines

    live_desc = describe(live_pem, run)
    committed_desc = describe(committed_pem, run)

    if live_key == committed_key:
        lines.append(
            f"{REPO} {CERT_PATH} carries the key the controller is serving. {live_desc}"
        )
        lines.append(
            "Compared as public keys, not as certificates — a re-issue around the same "
            "key seals identically and is deliberately not drift."
        )
        return 0, lines

    lines.append(
        f"SEAL CERT DRIFT — {REPO} {CERT_PATH} holds a different public key from the one "
        "sealed-secrets is serving. Every new seal goes to a key the controller has retired."
    )
    lines.append(f"  committed  {committed_desc}")
    lines.append(f"  live       {live_desc}")
    lines.append(
        "Nothing is broken yet: the controller retains old private keys, so existing "
        "SealedSecrets still unseal. It breaks the moment that retention ends."
    )
    lines.append("The fix, in one command from the bridge pod, then a PR to " + REPO + ":")
    lines.append(f"  curl -s {CONTROLLER} > {CERT_PATH}")
    return 2, lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--controller", default=CONTROLLER)
    parser.add_argument("--repo", default=REPO)
    args = parser.parse_args(argv)

    status, lines = report(fetch_live(args.controller), fetch_committed(args.repo))
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
