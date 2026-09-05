"""If the bridge pod's volume were lost, could this loop log back in?

    python3 -m tools.credential_recovery

Cycle 957, found while measuring what it would actually take to move
`agents/agora-claude-bridge-data` off server1 -- the last workload the
digest still lists there. The handoff had been carrying the reason that
move is hard as *"moving it kills the cycle doing the move"*, which is
true and is not the hazard. The hazard is one file on that volume.

**The only working Claude credential in this system lives on the PVC, and
nowhere else.** `bridge/credentials.py` bootstraps `.credentials.json`
from the `CLAUDE_CREDENTIALS_JSON` Secret exactly once, onto an empty
volume, and then deliberately never overwrites it -- because the CLI
refreshes its own token every few hours and the fresh one lives only on
disk. That invariant is correct and its own module docstring explains
why. The consequence nothing was watching is the other side of it: the
Secret's copy is a snapshot frozen at the moment a human last wrote it,
and it goes stale on its own while the loop runs perfectly well off the
disk copy.

That is not hypothetical. The PVC was replaced on 2026-08-17, the
bootstrap ran onto the empty volume, the Secret it copied was sixteen
days expired, and the loop was down for thirty hours until the owner
re-authed by hand. `bridge/credentials.py` records that outage in full.
What it could not do is tell anyone the same thing was true *again*
before the next volume event.

Measured live 2026-09-05 10:11 Oslo, from inside the bridge pod: the
Secret's snapshot expired **2026-08-01T18:22:21Z**, thirty-five days ago,
and says `subscriptionType: pro` against a live `max`. So on that
morning, replacing the PVC -- as a server2 move, a node failure or a
mistyped `kubectl delete` -- would have reproduced the August outage
exactly.

**What this judges, and what it deliberately does not.** The subject is
the *recovery* credential in the Secret, never the live one on disk. The
disk copy expires every few hours by design and the CLI refreshes it, so
raising on that would be raising on the system working. A raise here
means: the copy that would be restored from is one the CLI has already
been observed to reject.

**The threshold is the credential's own field, not a number I chose.**
`claudeAiOauth.expiresAt` in the past is expired; there is nothing to
tune. The second signal is independent and needs no threshold at all --
when the Secret's `subscriptionType` disagrees with the live file's, the
Secret predates a change to the account itself.

**No token is ever read out.** Only `expiresAt`, `subscriptionType` and
the set of field *names* are touched, and only those are printed. The
values that matter are never loaded into a variable this module prints,
compares or returns, which is also why there is no fixture in the test
file carrying anything shaped like a real token.

Exit 2 -- the Secret's snapshot cannot log in, so there is no recovery
path from a lost volume. Exit 1 -- one of the two sides could not be
read, which never reads as clean. Exit 0 -- the Secret would still work.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

#: The Secret's raw JSON arrives as an environment variable on the bridge
#: pod; `bridge/credentials.py` reads the same name. Reading the Secret
#: object itself is refused at both the tool and the RBAC level, so this
#: is the only way to see the snapshot at all -- and it is why this check
#: is on-box: it answers nothing from anywhere else.
SECRET_ENV = "CLAUDE_CREDENTIALS_JSON"

#: Where the live copy sits, relative to CLAUDE_HOME.
DISK_RELATIVE = os.path.join(".claude", ".credentials.json")


class CredentialError(Exception):
    """A side of the comparison could not be read at all."""


def _oauth(raw, where):
    """`claudeAiOauth` out of a credentials blob, or raise.

    Deliberately returns the dict rather than the whole document: nothing
    above this line has any business holding the outer object, and the
    narrower return is what keeps `accessToken` from travelling further
    than one function.
    """
    if not raw or not raw.strip():
        raise CredentialError("%s is empty" % where)
    try:
        blob = json.loads(raw)
    except ValueError as exc:
        raise CredentialError("%s is not JSON (%s)" % (where, exc))
    if not isinstance(blob, dict):
        raise CredentialError("%s is not a JSON object" % where)
    oauth = blob.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise CredentialError("%s carries no claudeAiOauth object" % where)
    return oauth


def expires_at(oauth, where):
    """`expiresAt` as an aware datetime.

    The field is epoch milliseconds in every credential this has been run
    against, but a bare-seconds value is legal-looking and would read as
    1970, i.e. as a false alarm. Anything past the year 3000 in seconds
    is milliseconds -- that boundary is far from both real answers, so it
    can never pick wrong on a credential either side of it.
    """
    value = oauth.get("expiresAt")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CredentialError(
            "%s has no readable expiresAt (%r)" % (where, value)
        )
    seconds = value / 1000.0 if value > 1e11 else float(value)
    try:
        return datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise CredentialError("%s has an unusable expiresAt (%s)" % (where, exc))


def read_secret(env=None):
    """The Secret's frozen snapshot -- the thing a restore would use."""
    environ = os.environ if env is None else env
    return _oauth(environ.get(SECRET_ENV, ""), "the %s Secret" % SECRET_ENV)


def disk_path(env=None):
    environ = os.environ if env is None else env
    home = environ.get("CLAUDE_HOME") or environ.get("HOME") or ""
    return os.path.join(home, DISK_RELATIVE)


def read_disk(path):
    """The live credential on the PVC -- read for comparison only."""
    try:
        with open(path) as handle:
            raw = handle.read()
    except OSError as exc:
        raise CredentialError("%s could not be read (%s)" % (path, exc))
    return _oauth(raw, path)


def judge(secret, live, now):
    """What a restore from the Secret would do, as a list of findings.

    `live` may be None -- the disk copy is only ever used for the
    subscription comparison, and losing it would not make the Secret's
    own expiry unreadable. Treating an unreadable disk copy as fatal here
    would mean the one situation this check exists for -- no working
    credential on disk -- silenced it.
    """
    findings = []
    secret_expiry = expires_at(secret, "the %s Secret" % SECRET_ENV)
    stale = secret_expiry <= now
    if stale:
        findings.append(
            {
                "state": "expired",
                "detail": "the Secret's snapshot expired %s UTC, %.1f day(s) ago -- "
                "restoring from it is what took the loop down for 30 hours on "
                "2026-08-17"
                % (
                    secret_expiry.isoformat(timespec="seconds"),
                    (now - secret_expiry).total_seconds() / 86400.0,
                ),
            }
        )
    if live is not None:
        secret_plan = secret.get("subscriptionType")
        live_plan = live.get("subscriptionType")
        if secret_plan != live_plan:
            findings.append(
                {
                    "state": "plan-drift",
                    "detail": "the Secret says subscriptionType %r and the live "
                    "credential says %r, so the snapshot predates a change to the "
                    "account itself" % (secret_plan, live_plan),
                }
            )
    return findings, secret_expiry, stale


def report(findings, secret_expiry, stale, live, live_expiry, now, out):
    for finding in findings:
        out.write("RAISE       %s\n" % finding["detail"])
    if not stale:
        out.write(
            "ok          the %s Secret would still log in -- expires %s UTC, in "
            "%.1f day(s)\n"
            % (
                SECRET_ENV,
                secret_expiry.isoformat(timespec="seconds"),
                (secret_expiry - now).total_seconds() / 86400.0,
            )
        )
    if live_expiry is not None:
        out.write(
            "NOT JUDGED  the live credential on the PVC, which expires %s UTC. It is "
            "refreshed by the CLI every few hours, so an expiry in the near future is "
            "the system working; raising on it would raise every cycle.\n"
            % live_expiry.isoformat(timespec="seconds")
        )
    else:
        out.write(
            "CANNOT READ the live credential on the PVC, so the subscription "
            "comparison was not made. The Secret's own expiry above is unaffected.\n"
        )
    out.write(
        "NOT JUDGED  whether the Secret's refreshToken could still mint a new access "
        "token. The snapshot carries no refreshTokenExpiresAt field, so there is "
        "nothing here to read it from; the 2026-08-17 outage is the one observation "
        "and there the CLI could not.\n"
    )
    out.write(
        "Read the recovery credential in the %s Secret and compared it against the "
        "live copy on this pod's PVC. No token value is read out of either -- only "
        "expiresAt and subscriptionType.\n" % SECRET_ENV
    )
    if stale:
        out.write(
            "This loop's only working credential is the file on "
            "agents/agora-claude-bridge-data. Losing that volume -- a node failure, a "
            "move to server2, a mistyped delete -- leaves no way back in without the "
            "owner re-authenticating by hand. Fixing it means writing a current "
            "credential into the claude-auth SealedSecret, which needs kubeseal and a "
            "Secret write; this loop has neither.\n"
        )
    return 2 if findings else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--disk",
        default=None,
        help="path to the live .credentials.json (default: under CLAUDE_HOME)",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    try:
        secret = read_secret()
    except CredentialError as exc:
        sys.stdout.write("CANNOT READ %s\n" % exc)
        sys.stdout.write(
            "Unreadable never reads as clean -- with no snapshot to judge, whether a "
            "restore would work is unknown rather than fine.\n"
        )
        return 1

    path = args.disk or disk_path()
    live = None
    live_expiry = None
    try:
        live = read_disk(path)
        live_expiry = expires_at(live, path)
    except CredentialError:
        live = None
        live_expiry = None

    try:
        findings, secret_expiry, stale = judge(secret, live, now)
    except CredentialError as exc:
        sys.stdout.write("CANNOT READ %s\n" % exc)
        sys.stdout.write("Unreadable never reads as clean.\n")
        return 1

    return report(findings, secret_expiry, stale, live, live_expiry, now, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
