"""What can this loop actually do on the owner's NAS right now?

Cycle 663. Every other NAS check in this package asks a question *about the
box* -- which ports listen, which apps are behind, where the downloads go.
None of them asks the question that decides whether any of those findings can
be acted on: **what privilege do I hold on that machine, this cycle.**

The answer had been carried in prose instead, and prose decayed. The owner's
sudo grant started as a 24-hour password window on issue #122, and my handoff
said "root is time-boxed, revoked end of day 2026-08-30" for four cycles. It
was wrong before it was written: he had already replaced the window with a
standing `/etc/sudoers.d/nova-full-access` carrying `nova ALL=(ALL) NOPASSWD:
ALL`, said so on the row, and asked me to verify it. Cycles 658, 659 and 662
each wrote a sentence about being blocked on a paste that had already
happened, and 662 put a three-way question to him at the top of the digest --
how should I reach the Docker daemon after tonight -- whose answer was sitting
two comments above it. Measuring `sudo -n true` costs one call and would have
answered all four.

So this is not a security alarm. It is an instrument, and the failure it
removes is a specific one: **a capability written down once and inherited
thereafter.** `personality.md` already says an "I can't" is a measurement
rather than a conclusion; this is that rule given something to read.

**No exit 2, deliberately.** `tools.nas` sets the precedent in its own
docstring -- *this is a question, not an alarm* -- and the reasoning carries
here with one addition. The owner narrowing my access on his own box is an
ordinary thing for him to do, and a check that goes red when he does it is a
check that trains me to ignore it (`tools.nas_ports` makes the same argument
about a baseline port). What is *not* ordinary is not knowing, so 1 is
reserved for exactly that: no ssh binary, no readable key, a failed hop, or
output this cannot parse. **An unmeasured privilege must never read as a
measured one**, in either direction -- neither "I still have root" nor "I lost
it" may be printed from a call that did not complete.

**The transport is the one this package already owns.** One constant remote
command with no variable part at all, same contract as
`tools.nas_ports.LISTENER_COMMAND`, so a shell on the far side cannot be
talked into anything. It does not use `nas._run_ssh`, which hardcodes `curl
--config -` and raises on a non-zero exit; here a non-zero result from a
probed sub-command is the *finding* rather than an error, and only ssh itself
failing (255) means nothing was measured.

**What it does not see.** It reads *my own* privilege, not anyone else's -- it
says nothing about who else can reach that box, which is
`tools.nas_ports`' question. It is current state, so a grant added and
removed between two cycles leaves no trace. And it probes the Docker daemon
through `sudo` only: an unprivileged path to the socket would not show here,
because the socket is `srw-rw---- root root` today and adding `nova` to a
`docker` group is one of the shapes of this the owner has not chosen.
"""

import argparse
import subprocess
import sys

from tools import nas

#: One constant string, no interpolation, no stdin. Each line is `key=value`
#: so a partial answer is a parse failure rather than a silently short report.
#: The two Docker paths are both real on DSM 7.1.1 (`/usr/local/bin/docker` is
#: a symlink into the package tree); `sudo` resets `PATH` to a secure_path
#: that contains neither, which is why a bare `sudo docker ps` answers
#: `command not found` on a box where root can plainly drive Docker.
PRIVILEGE_COMMAND = (
    "echo user=$(id -un); "
    "sudo -n true >/dev/null 2>&1 && echo root=yes || echo root=no; "
    "d=; for p in /usr/local/bin/docker "
    "/var/packages/ContainerManager/target/usr/bin/docker "
    "/var/packages/Docker/target/usr/bin/docker; do "
    "[ -x \"$p\" ] && d=$p && break; done; "
    "echo dockerbin=${d:-none}; "
    "if [ -n \"$d\" ]; then "
    "sudo -n \"$d\" ps -q >/dev/null 2>&1 && echo docker=yes || echo docker=no; "
    "else echo docker=nobinary; fi; "
    "sudo -n cat /etc/sudoers.d/nova-full-access >/dev/null 2>&1 "
    "&& echo standing=yes || echo standing=no"
)

#: Every key `PRIVILEGE_COMMAND` promises. A missing one is `Unreadable`
#: rather than a default, because the whole point of this check is that an
#: unmeasured capability must not be printed as a measured one.
REQUIRED = ("user", "root", "dockerbin", "docker", "standing")


class Unreadable(Exception):
    """The hop did not complete, or its output was not the promised shape."""


def parse(text):
    """`key=value` lines into a dict, raising when a promised key is absent."""
    found = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    missing = [k for k in REQUIRED if k not in found]
    if missing:
        raise Unreadable(f"the probe did not answer: {', '.join(missing)}")
    return found


def probe(ssh, run=subprocess.run, timeout=30):
    """Run the one constant command. Only ssh itself failing is fatal."""
    argv = ["ssh", "-i", ssh["key"], *nas.SSH_OPTS,
            f"{ssh['user']}@{ssh['host']}", PRIVILEGE_COMMAND]
    try:
        done = run(argv, input=None, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise Unreadable(f"the NAS did not answer within {timeout}s") from exc
    except OSError as exc:
        raise Unreadable(f"could not start ssh: {exc}") from exc
    if done.returncode == 255:
        raise Unreadable(
            f"ssh to {ssh['host']} failed: {(done.stderr or '').strip() or 'no detail'}")
    return parse(done.stdout or "")


def report(env=None, out=sys.stdout, ssh=nas._UNSET, run=subprocess.run):
    conf = nas.ssh_config(env=env) if ssh is nas._UNSET else ssh
    if not conf:
        print("CANNOT SEE -- this pod has no ssh binary or no readable key for the NAS, so "
              "nothing below is a claim about what I can do there.", file=out)
        print("Judged 0 host(s). An unmeasured privilege is not a privilege I have and not "
              "one I have lost.", file=out)
        return 1

    try:
        found = probe(conf, run=run)
    except Unreadable as exc:
        print(f"CANNOT SEE -- {exc}", file=out)
        print(f"Judged 0 host(s) of 1 ({conf['host']}). An unmeasured privilege is not a "
              "privilege I have and not one I have lost.", file=out)
        return 1

    root = found["root"] == "yes"
    docker = found["docker"] == "yes"
    print(f"ON {conf['host']} I am {found['user']}, and "
          f"{'I can become root without a password' if root else 'I cannot become root'}.",
          file=out)
    if root:
        where = ("present" if found["standing"] == "yes"
                 else "absent -- root comes from somewhere else")
        print(f"  standing grant file /etc/sudoers.d/nova-full-access: {where}", file=out)
    print(f"  docker daemon: {'reachable through sudo' if docker else 'not reachable'} "
          f"(binary {found['dockerbin']})", file=out)
    print(f"Judged my own privilege on 1 host of 1 ({conf['host']}), over the SSH hop. This "
          "is current state and it is about me only -- who else can reach that box is "
          "tools.nas_ports' question.", file=out)
    return 0


def main(argv=None, env=None, out=sys.stdout, ssh=nas._UNSET, run=subprocess.run):
    argparse.ArgumentParser(
        prog="python3 -m tools.nas_privilege",
        description=__doc__.split("\n")[0],
    ).parse_args(argv)
    return report(env=env, out=out, ssh=ssh, run=run)


if __name__ == "__main__":
    sys.exit(main())
