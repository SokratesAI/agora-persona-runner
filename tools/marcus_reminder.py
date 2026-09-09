"""Did last night's Marcus reminder actually reach a phone, or only exit 0?

Cycle 1252, off the top row of the owner's board (idea #187, Marcus, a project he
rates 🔴 Immediately). The 20:00 CronJob `marcus-reminder` is built end to end
-- RFC 8291 encryption, VAPID, a declarative payload Safari renders itself --
and its own script already names the three outcomes apart in `outcome()`:
`delivered`, `nobody` (the subscription store is empty) and `failed` (a
subscribed device refused it). **Nothing reads those verdicts.** They are
printed to a container log that dies with the Job, and Kubernetes keeps three
successful Jobs, so the record of whether Marcus has ever notified him
survives about three nights and then is gone.

    python3 -m tools.marcus_reminder

**`tools.cronjob_health` does not cover this and cannot.** It counts scheduled
slots between `lastScheduleTime` and `lastSuccessfulTime`, so it answers "did
the job run and exit 0". Every state this check exists to separate exits 0:
a rest day sends nothing, an empty subscription store sends nothing, and a
delivered notification sends something -- one exit code, three facts. The
reminder script's own docstring says it in the same words: *"a job that exits
0 having sent nothing is the state this whole row already spent three cycles
in."* The verdict is in the log line and only in the log line.

**It reads the live cluster, never git**, the same call `cronjob_health` and
`argocd_health` make, and for a sharper reason here: the thing being judged is
a sentence a process printed, which exists in exactly one place.

**`nobody` raises, and that is a deliberate call against the script's own.**
The script does not fail its run on `nobody` because a CronJob that goes red
nightly is one nobody reads, and that is right for a job. It is wrong for a
check a cycle reads before it picks work: "a 🔴 Immediately feature is built,
running nightly and delivering nothing" is actionable, the action is to ask
the owner to tap **Enable reminders** in Marcus, and the day he taps it this goes
quiet by itself and stays quiet. `preflight` collapses an unchanged standing
finding to one line, which is the interface that makes a standing raise
readable rather than noise.

**A rest day is not a finding.** Wednesday and Sunday are `Rest` in the
current block, so two nights in seven the correct behaviour is silence, and a
check that could not tell that from a broken send would be red 29% of the time
for nothing.

**Exit contract.** 2 when the newest run I can read is `nobody`, `refused`,
`misconfigured` or a line I do not recognise. 1 when the cluster could not be
read, or when no reminder Pod's log is readable at all -- an unreadable
history is not a healthy one. 0 when the newest readable run delivered, or was
a genuine rest day.

**What it cannot do, said here rather than discovered later:** it sees only the
Jobs Kubernetes has kept, which is three successes and three failures. It is a
reader of a short window, not an archive, and the fix for that is durable
storage on Marcus's side rather than a longer look from here.
"""

import argparse
import json
import subprocess
import sys

NAMESPACE = "agents"
SELECTOR = "app=marcus-reminder"

#: Each verdict, and whether reading it should raise. The strings are the
#: script's own output in `agents/marcus-reminder.py`, matched as substrings of
#: the whole log rather than re-derived here -- a second copy of the wording
#: would drift from the process that prints it with nothing failing.
#:
#: Order matters: a run that delivered also mentions the payload title, and a
#: refusal is reported alongside counts, so the specific phrases are tried
#: before the general ones.
VERDICTS = (
    ("NO DEVICE IS SUBSCRIBED", "nobody", True,
     "the reminder is wired end to end and no phone has subscribed -- "
     "ask the owner to open Marcus and tap Enable reminders"),
    ("every subscribed device refused it", "refused", True,
     "a phone is subscribed and the push service refused the send"),
    ("send refused with HTTP", "misconfigured", True,
     "the send route itself refused the job -- a bad token, or sending is "
     "not configured on that pod"),
    ("delivered to ", "delivered", False,
     "the notification reached his phone"),
    ("nothing planned for", "rest day", False,
     "tomorrow is a rest day in the plan, so silence is correct"),
)


def read_pods(runner=subprocess.run):
    """Every reminder Pod Kubernetes still has, newest start first.

    Returns `(pods, error)`. A Pod rather than a Job because the log lives on
    the Pod and a Job's own status carries no verdict at all -- every state
    this check separates is a `Complete` Job.
    """
    cmd = ["kubectl", "get", "pods", "-n", NAMESPACE, "-l", SELECTOR, "-o", "json"]
    try:
        done = runner(cmd, capture_output=True, text=True, timeout=60)
    except Exception as err:  # noqa: BLE001 - any failure to run kubectl is the same fact
        return [], f"could not run kubectl: {err}"
    if done.returncode != 0:
        return [], f"kubectl exited {done.returncode}: {(done.stderr or '').strip()[:300]}"
    try:
        items = json.loads(done.stdout or "{}").get("items") or []
    except json.JSONDecodeError as err:
        return [], f"kubectl returned something that is not JSON: {err}"
    pods = []
    for item in items:
        meta = item.get("metadata") or {}
        status = item.get("status") or {}
        pods.append({
            "name": meta.get("name") or "",
            # `startTime` and not `creationTimestamp`: a Pod that never got
            # scheduled has no start and sorts last, which is where an
            # unreadable one belongs.
            "started": status.get("startTime") or "",
            "phase": status.get("phase") or "",
        })
    pods.sort(key=lambda p: p["started"], reverse=True)
    return pods, None


def read_log(pod, runner=subprocess.run):
    """That Pod's whole log, or `None` if it cannot be read.

    `None` is not an empty log: a Pod whose log has been reaped and a run that
    printed nothing are different facts, and only the second one is a finding
    about the reminder.
    """
    cmd = ["kubectl", "logs", "-n", NAMESPACE, pod, "--tail=200"]
    try:
        done = runner(cmd, capture_output=True, text=True, timeout=60)
    except Exception:  # noqa: BLE001
        return None
    if done.returncode != 0:
        return None
    return done.stdout or ""


def classify(text):
    """`(verdict, raises, explanation)` for one run's log.

    An unrecognised log raises. The alternative is to read it as healthy, and
    the whole point of this check is that "printed something I do not
    understand" and "delivered a notification" must not share an exit code.
    """
    if text is None:
        return ("unreadable", True, "the log for this run could not be read")
    for needle, verdict, raises, explanation in VERDICTS:
        if needle in text:
            return (verdict, raises, explanation)
    return ("unrecognised", True,
            "the run printed nothing this check knows how to read")


def _newest_line(pod, verdict, explanation):
    started = pod["started"] or "no start time"
    return f"{verdict.upper():<14} {pod['name']}  (started {started} UTC) -- {explanation}"


def main(argv=None, runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3,
                        help="how many of the kept runs to print (default 3)")
    args = parser.parse_args(argv)

    pods, error = read_pods(runner)
    if error:
        print(f"COULD NOT READ the reminder Pods -- {error}")
        return 1
    if not pods:
        # No Pod at all is not a silent reminder: `cronjob_health` owns whether
        # the CronJob is firing, and saying so here keeps the two apart.
        print("no marcus-reminder Pod exists in this cluster -- "
              "tools.cronjob_health owns whether the CronJob is firing at all.")
        return 1

    rows = []
    for pod in pods[: max(1, args.runs)]:
        text = read_log(pod["name"], runner)
        verdict, raises, explanation = classify(text)
        rows.append((pod, verdict, raises, explanation))

    if all(verdict == "unreadable" for _, verdict, _, _ in rows):
        print(f"COULD NOT READ any of the {len(rows)} kept reminder run(s) -- "
              "an unreadable history is not a healthy one.")
        return 1

    print(f"The {len(rows)} newest of {len(pods)} kept marcus-reminder run(s), newest first:")
    for pod, verdict, _, explanation in rows:
        print("  " + _newest_line(pod, verdict, explanation))

    pod, verdict, raises, explanation = rows[0]
    if raises:
        print()
        print(f"The newest run is {verdict.upper()}: {explanation}.")
        if verdict == "nobody":
            print("  Nothing here is broken and there is no pull request that fixes it. "
                  "This raises because a 🔴 Immediately feature running nightly and "
                  "delivering nothing is worth one line in front of every cycle, and "
                  "it goes quiet for good the moment a phone subscribes.")
        return 2
    print()
    print(f"The newest run is {verdict.upper()} -- nothing to act on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
