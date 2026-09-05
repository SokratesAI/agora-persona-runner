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

Measured 2026-09-05 10:11 Oslo, from inside the bridge pod -- and note
what "from inside the bridge pod" costs, because cycle 959 paid it: the
snapshot is read from an environment variable, which the kubelet freezes
at container start and never rewrites, so it is the Secret *as of this
pod's start* rather than the live one. `env_is_stale` below is what
separates those two. The reading itself: the
Secret's snapshot expired **2026-08-01T18:22:21Z**, thirty-five days ago,
and says `subscriptionType: pro` against a live `max`. So on that
morning, replacing the PVC -- as a server2 move, a node failure or a
mistyped `kubectl delete` -- would have reproduced the August outage
exactly.

**Two questions now, and only one of them is about the Secret.**
`judge_live_refresh` answers *when does this loop's login die*, off the
live credential's `refreshTokenExpiresAt` -- a date that counts down from
the last interactive login and that nothing in this loop can move. It runs
first and prints before any branch below can return, because the Secret's
half is unreachable on a pod whose environment predates the last reseal
while the login deadline is readable either way. The measurement behind it
is in that function, and it retires the handoff's plan of a periodic
reseal: a reseal copies the credential on disk, and the credential on disk
dies on the same day whether it is copied or not.

**What the Secret half judges, and what it deliberately does not.** The subject is
the *recovery* credential in the Secret, never the live one on disk. The
disk copy expires every few hours by design and the CLI refreshes it, so
raising on that would be raising on the system working. A raise here
means: the copy that would be restored from is one the CLI has already
been observed to reject.

**The threshold is the credential's own field, not a number I chose.**
`claudeAiOauth.refreshTokenExpiresAt` in the past means the snapshot can
mint nothing and a restore from it cannot log in; there is nothing to
tune. On an older snapshot that has no such field the fallback is
`expiresAt`, which is conservative and is the 2026-08-17 condition.
Judging `expiresAt` on a *current* snapshot would be wrong: the access
token is short-lived by design, so a Secret resealed this morning would
raise this afternoon and the check could never be satisfied. The second signal is independent and needs no threshold at all --
when the Secret's `subscriptionType` disagrees with the live file's, the
Secret predates a change to the account itself.

**No token is ever read out.** Only the two expiry fields, `subscriptionType` and
the set of field *names* are touched, and only those are printed. The
values that matter are never loaded into a variable this module prints,
compares or returns, which is also why there is no fixture in the test
file carrying anything shaped like a real token.

Exit 2 -- the Secret's snapshot cannot log in, so there is no recovery
path from a lost volume. Exit 1 -- the verdict could not be reached:
either a side was unreadable, or the environment variable is provably
older than the Secret (`env_is_stale`), which is a different reason and
is deliberately not a 2. Neither ever reads as clean. Exit 0 -- the
Secret would still work.
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

#: The SealedSecret the controller unseals into that Secret. Its
#: `Synced` condition carries `lastUpdateTime`, which is when the
#: controller last *wrote* the Secret -- readable without reading a
#: Secret, which RBAC refuses here.
SEALED_NAME = "claude-auth"
SEALED_NAMESPACE = "agents"


def _kubectl(args):
    """One `kubectl get -o jsonpath` read, or None if it cannot be made."""
    import subprocess

    try:
        done = subprocess.run(
            ["kubectl"] + args, capture_output=True, text=True, timeout=20
        )
    except Exception:  # absent binary, timeout, or no API route
        return None
    if done.returncode != 0:
        return None
    value = done.stdout.strip()
    return value or None


def _stamp(value):
    """An RFC3339 stamp from the API server as an aware datetime, or None.

    `fromisoformat` rather than a `strptime` format, because a format
    string only accepts the one shape it spells. `metav1.Time` marshals
    whole seconds and a literal `Z`, but a plain Go `time.Time` carries
    fractional seconds and an offset may be numeric -- and a value that
    is present and unparsable would fall into the *unknown* branch,
    which prints a caveat and changes no verdict. That is a gate that
    silently stops gating, which is the failure shape this loop pays for
    most often.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def sealed_written_at(read=None):
    """When the controller last wrote the Secret this env var came from."""
    reader = _kubectl if read is None else read
    return _stamp(
        reader(
            [
                "get",
                "sealedsecret",
                SEALED_NAME,
                "-n",
                SEALED_NAMESPACE,
                "-o",
                'jsonpath={.status.conditions[?(@.type=="Synced")].lastUpdateTime}',
            ]
        )
    )


def pod_started_at(read=None, env=None):
    """When the pod holding this environment started."""
    environ = os.environ if env is None else env
    name = environ.get("HOSTNAME") or ""
    if not name:
        return None
    reader = _kubectl if read is None else read
    return _stamp(
        reader(
            [
                "get",
                "pod",
                name,
                "-n",
                SEALED_NAMESPACE,
                "-o",
                "jsonpath={.status.startTime}",
            ]
        )
    )


def env_is_stale(pod_start, sealed_written):
    """Is the snapshot in this environment provably not the current Secret?

    A Secret projected as an environment variable is frozen at the moment
    the container started; the kubelet never rewrites it. So the value
    this check reads is the Secret *as of pod start*, and the docstring
    above -- "measured live" -- was never true of it.

    That is not pedantry. Cycle 958 resealed the credential and the
    controller wrote the Secret at 2026-09-05 08:37 UTC; this pod started
    2026-09-04 20:50 UTC, twelve hours earlier, so on the very next cycle
    this check reported the expired snapshot as current and told cycle
    959 to go and do the reseal that had already landed. It fails the
    other way too and worse: once a pod has started with a good snapshot,
    a later bad reseal is invisible here until the next restart, so the
    check can report clean about a Secret it cannot see.

    Both timestamps come from the API server rather than from the Secret,
    which RBAC refuses to show this account at all. If either is
    unreadable the answer is None -- unknown, not fresh -- because a
    freshness claim nothing measured is the thing this function exists to
    stop.
    """
    if pod_start is None or sealed_written is None:
        return None
    # `>=`, not `>`: these stamps are whole seconds, so a write inside the
    # start second could have gone either way, and the safe reading of
    # "I cannot tell" is not "the value is current".
    return sealed_written >= pod_start


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
    return _epoch(value, where, "expiresAt")


def _epoch(value, where, field):
    seconds = value / 1000.0 if value > 1e11 else float(value)
    try:
        return datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise CredentialError("%s has an unusable %s (%s)" % (where, field, exc))


def recovery_expiry(oauth, where):
    """The field that decides whether a restore can log in, and its name.

    `expiresAt` is the access token, which is minted from the refresh
    token and is *meant* to be short-lived -- a snapshot taken this
    morning carries one that dies this afternoon. Judging it means a
    freshly resealed Secret raises within hours of being resealed, which
    is a check that cannot be satisfied. What a restore actually needs is
    a live refresh token, so `refreshTokenExpiresAt` is the threshold
    whenever the snapshot carries it.

    Older snapshots do not carry it -- the one this check was written
    against is exactly that case -- so the fallback is `expiresAt`, which
    is the previous behaviour and is conservative: an expired access
    token on a snapshot with no refresh expiry is the 2026-08-17
    condition and should still raise.
    """
    if isinstance(oauth.get("refreshTokenExpiresAt"), (int, float)) and not isinstance(
        oauth.get("refreshTokenExpiresAt"), bool
    ):
        return _epoch(oauth["refreshTokenExpiresAt"], where, "refreshTokenExpiresAt"), (
            "refreshTokenExpiresAt"
        )
    return expires_at(oauth, where), "expiresAt"


#: How long this loop stayed down the one time its login lapsed: the
#: credential expired on 2026-08-17 and the owner re-authenticated by
#: hand thirty hours later (`bridge/credentials.py` records it). It is
#: the only measurement anyone has of how long recovery takes here, so
#: it is the margin below which there is no room left to recover at all.
OUTAGE_HOURS = 30.0

#: How long the owner can go without reading anything I write. Measured
#: 2026-09-05 over the 226 comments he has left on journal cards between
#: 2026-08-10 and 2026-09-05: the median gap between two of them is 0.4
#: hours and the 95th percentile is 12.9, but the longest single silence
#: in those 26 days is 56.7 hours and one gap in 225 ran past 30. So a
#: number below this can be raised into a stretch he is demonstrably not
#: reading, and the alarm would be true and unheard.
OWNER_SILENCE_HOURS = 57.0

#: The lead an alarm about the login actually needs. It has to reach a
#: human *and then* leave room for the recovery itself, so it is the two
#: added together rather than either one. At `OUTAGE_HOURS` alone, today's
#: 2026-09-16 deadline first raises on 2026-09-15 -- inside a silence he
#: has already had once this month, which is the failure this constant
#: exists to stop rather than a margin anyone chose to be comfortable.
LOGIN_LEAD_HOURS = OWNER_SILENCE_HOURS + OUTAGE_HOURS


def judge_live_refresh(live, now):
    """When does this loop's login die, and can it renew it itself?

    It cannot, and that is the whole reason this is judged separately
    from the Secret's snapshot. The CLI refreshes its own *access* token
    every few hours off the refresh token and rewrites the file; it does
    not mint a new refresh token, so `refreshTokenExpiresAt` counts down
    from the last interactive login and nothing this loop does moves it.

    Measured 2026-09-05, and the measurement is what this function is
    for. The file on the PVC was rewritten at 05:01:47.700 UTC that
    morning -- `expiresAt` 13:01:47.699, eight hours out, so the refresh
    ran. `refreshTokenExpiresAt` came back 2026-09-16T21:08:31.699, the
    same millisecond fraction as the access token's, so the server
    supplied both in that same response and the refresh token's deadline
    was **not** extended by using it. Thirty days before that stamp is
    2026-08-17T21:08 -- the hand re-auth that ended the outage. So the
    window is a fixed thirty days from a human login, and on 2026-09-16
    this loop stops exactly as it did on 2026-08-17.

    The consequence for the handoff: resealing the Secret periodically
    cannot prevent that. A reseal copies the credential on disk, and the
    credential on disk dies on the same day whether it is copied or not.
    Only an interactive login moves the date.

    It raises at `LOGIN_LEAD_HOURS` rather than at `OUTAGE_HOURS`, and
    names which of the two it tripped. `OUTAGE_HOURS` is how long the
    recovery took once it had started; it says nothing about getting the
    ask in front of the one person who can perform it, and an alarm can
    be raised entirely inside a silence he is not reading.

    Returns (findings, expiry) with expiry None when the file carries no
    refresh expiry. There is deliberately no fallback to `expiresAt`
    here: on the *live* copy that field is eight hours wide by design, so
    judging it would raise every afternoon on a system that is working --
    which is the trap `recovery_expiry` documents one function up, and
    the reason its fallback is safe there and would not be safe here.
    """
    findings = []
    raw = live.get("refreshTokenExpiresAt")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return findings, None
    expiry = _epoch(raw, "the live credential", "refreshTokenExpiresAt")
    hours_left = (expiry - now).total_seconds() / 3600.0
    if hours_left <= LOGIN_LEAD_HOURS:
        if hours_left <= OUTAGE_HOURS:
            margin = (
                "inside the %.0f hour(s) the 2026-08-17 recovery took, so there is "
                "no margin left" % OUTAGE_HOURS
            )
        else:
            margin = (
                "inside the %.0f hour(s) an alarm needs to reach him and still leave "
                "room to recover -- his longest measured silence (%.0f) plus the "
                "%.0f the 2026-08-17 recovery took"
                % (LOGIN_LEAD_HOURS, OWNER_SILENCE_HOURS, OUTAGE_HOURS)
            )
        findings.append(
            {
                "state": "login-expiring",
                "detail": "the live credential's refresh token %s %s UTC, %.1f hour(s) "
                "%s -- %s. Nothing in this loop can renew it: the CLI "
                "refreshes the access token and leaves this date where it is. It takes "
                "an interactive login by the owner."
                % (
                    "expired" if hours_left <= 0 else "expires",
                    expiry.isoformat(timespec="seconds"),
                    abs(hours_left),
                    "ago" if hours_left <= 0 else "away",
                    margin,
                ),
            }
        )
    return findings, expiry


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
    secret_expiry, field = recovery_expiry(secret, "the %s Secret" % SECRET_ENV)
    stale = secret_expiry <= now
    if stale:
        findings.append(
            {
                "state": "expired",
                "detail": "the Secret's snapshot expired %s UTC, %.1f day(s) ago "
                "(judged on %s) -- restoring from it is what took the loop down for "
                "30 hours on 2026-08-17"
                % (
                    secret_expiry.isoformat(timespec="seconds"),
                    (now - secret_expiry).total_seconds() / 86400.0,
                    field,
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
    return findings, secret_expiry, stale, field


def report(findings, secret_expiry, stale, live, live_expiry, now, out, field="expiresAt"):
    for finding in findings:
        out.write("RAISE       %s\n" % finding["detail"])
    if not stale:
        out.write(
            "ok          the %s Secret would still log in -- its %s is %s UTC, in "
            "%.1f day(s)\n"
            % (
                SECRET_ENV,
                field,
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
    if field == "expiresAt":
        out.write(
            "NOT JUDGED  whether the Secret's refreshToken could still mint a new "
            "access token. This snapshot carries no refreshTokenExpiresAt field, so "
            "there is nothing here to read it from; the 2026-08-17 outage is the one "
            "observation and there the CLI could not. A snapshot resealed from a "
            "current credential does carry it and is judged on it instead.\n"
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
            "owner re-authenticating by hand. Fixing it takes no Secret write and no "
            "cluster privilege: download kubeseal, seal the live credential on this "
            "pod's PVC against platform-config's own secrets/sealed-secrets-pub.pem "
            "at strict scope with the four keys secrets/seal-secrets.sh builds, check "
            "it against the controller's POST /v1/verify in kube-system (200 decrypts, "
            "409 does not), and open a pull request replacing "
            "secrets/sealed/agents-claude-auth.yaml. Done that way at cycle 958, "
            "platform-config#704.\n"
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

    # The live copy is read first, and its own verdict is printed before any
    # branch below can return. The snapshot's verdict is unreachable on a pod
    # whose environment predates the last reseal (`env_is_stale`), and that
    # early return used to take the login deadline down with it -- two
    # questions with one exit, where only one of them was unanswerable.
    path = args.disk or disk_path()
    try:
        live = read_disk(path)
        live_expiry = expires_at(live, path)
    except CredentialError:
        live = None
        live_expiry = None

    login_status = 0
    if live is None:
        sys.stdout.write(
            "CANNOT READ the live credential at %s, so when this loop's login "
            "expires is unknown.\n" % path
        )
        login_status = 1
    else:
        login_findings, login_expiry = judge_live_refresh(live, now)
        if login_expiry is None:
            sys.stdout.write(
                "NOT JUDGED  when this loop's login expires -- the live credential "
                "carries no refreshTokenExpiresAt. There is deliberately no fallback "
                "to expiresAt here; that field is hours wide on the live copy.\n"
            )
            login_status = 1
        elif login_findings:
            for finding in login_findings:
                sys.stdout.write("RAISE %s\n" % finding["detail"])
            login_status = 2
        else:
            sys.stdout.write(
                "This loop's login expires %s UTC, %.1f day(s) away. Only an "
                "interactive login by the owner moves that date -- the CLI's own "
                "refresh does not, and neither does resealing the Secret.\n"
                % (
                    login_expiry.isoformat(timespec="seconds"),
                    (login_expiry - now).total_seconds() / 86400.0,
                )
            )

    try:
        secret = read_secret()
    except CredentialError as exc:
        sys.stdout.write("CANNOT READ %s\n" % exc)
        sys.stdout.write(
            "Unreadable never reads as clean -- with no snapshot to judge, whether a "
            "restore would work is unknown rather than fine.\n"
        )
        return max(1, login_status)

    sealed_written = sealed_written_at()
    pod_start = pod_started_at()
    stale_env = env_is_stale(pod_start, sealed_written)
    if stale_env:
        sys.stdout.write(
            "CANNOT JUDGE the %s snapshot in this pod's environment is not the "
            "Secret's current contents. The controller wrote that Secret %s UTC and "
            "this pod started %s UTC, so the value read here is the pre-write copy -- "
            "an environment variable projected from a Secret is frozen when the "
            "container starts and is never rewritten.\n"
            % (
                SECRET_ENV,
                sealed_written.isoformat(timespec="seconds"),
                pod_start.isoformat(timespec="seconds"),
            )
        )
        sys.stdout.write(
            "Unreadable never reads as clean, and it is deliberately not a RAISE "
            "either: raising here sends a cycle to redo a reseal that has already "
            "landed, which is exactly what happened to cycle 959. This becomes "
            "readable again the next time the bridge pod restarts.\n"
        )
        return max(1, login_status)

    freshness_note = None
    if stale_env is None:
        freshness_note = (
            "NOT JUDGED  whether the snapshot in this pod's environment is the "
            "Secret's current contents. That needs the SealedSecret's Synced "
            "condition and this pod's startTime from the API server, and one of "
            "them was unreadable here -- so the verdict below is about the Secret "
            "as of this pod's start, which is all an environment variable can ever "
            "carry.\n"
        )

    try:
        findings, secret_expiry, stale, field = judge(secret, live, now)
    except CredentialError as exc:
        sys.stdout.write("CANNOT READ %s\n" % exc)
        sys.stdout.write("Unreadable never reads as clean.\n")
        return max(1, login_status)

    if freshness_note:
        sys.stdout.write(freshness_note)
    return max(
        login_status,
        report(findings, secret_expiry, stale, live, live_expiry, now, sys.stdout, field),
    )


if __name__ == "__main__":
    sys.exit(main())
