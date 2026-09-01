# nova-watch — the watcher that survives the box it watches

The owner's idea #113, and the half of his issue #103 that needed no hardware to write. The design is `nova/resources/research/backup-node-2026-08.md` in the vault.

On 2026-08-24 the server went down and his phone stayed quiet. The stall notifier was not broken: it runs inside `nova-site`, which posts to `agora`, which signs the Web Push — three hops, all three on `server1`, which is the only node this cluster has. A watcher for a machine cannot run on that machine.

So this program is meant to run somewhere else. It polls the loop from outside and reports **two separate things**, never merged into one:

- `UNREACHABLE` — the cluster does not answer at all. The box, the network or Tailscale is down, and from outside it cannot tell which. Needs two consecutive failed polls, because home broadband blips look identical to Hetzner dying.
- `SILENT` — the cluster answers and the loop has stopped writing. That is the runner pod, not the machine.

It reaches the phone with Web Push over VAPID, which is sender-side only: the message is signed here and POSTed straight to Google's, Apple's or Mozilla's push service. Nothing in that path goes through the server being watched — which is exactly why an alarm built on it can fire during the outage it exists for.

## What it needs

| Variable | What it is |
|---|---|
| `NOVA_WATCH_SUBSCRIPTION` | the push subscription record, as JSON. One file, one operator: `/data/subscription.json` on the `agora` PVC. |
| `VAPID_PRIVATE_KEY` | from Secret `agora-vapid` in namespace `agents`. |
| `VAPID_PUBLIC_KEY` | **not needed** — `pywebpush` derives it from the private key. It is in the same Secret; I had it in this table until my reviewer pointed out nothing reads it, which is one fewer value to copy off the cluster. |
| `NOVA_WATCH_URL` | optional; defaults to the tailnet journal endpoint — which **does not resolve and does not route from the NAS today**. See the section above. |
| `NOVA_WATCH_INTERVAL` | optional; seconds between polls, default 300. |

It refuses to start without the first two rather than running as a watchdog that cannot speak. A silent watchdog is worse than none, because it looks like coverage.

**Both of those values still have to be handed over by a human, and they are no longer the first thing in the way** — see the section above: the NAS has no tailnet route, so this program cannot poll from there even with both secrets in hand.

**Both of those values still have to be handed over by a human.** Reading a Kubernetes Secret and `exec`ing into a pod are both refused for a Nova cycle, at the tool layer and at RBAC. That is step 1 of the design — prove one real send from a machine that is not `server1` — and it is the only step here that a cycle cannot do alone. Copying the VAPID private key to the NAS widens where that key lives, which is a real trade and the owner's to refuse; the alternative is an alarm that cannot ring during the outage it exists for.

## The NAS cannot reach the tailnet, and that blocks this before the secrets do

Measured 2026-08-30 (Cycle 664) over the SSH hop. Earlier cycles have had a shell on that box since 2026-08-29; none of them had asked it this question. **Every ordinary program on the NAS — `curl`, `python3`, and therefore this container — cannot reach a tailnet address at all.** The default `NOVA_WATCH_URL` is a tailnet name, so as things stand this watcher would start and never once poll successfully.

What was measured, and what each measurement rules out:

- `tailscale ping 100.89.73.98` (nova) and `tailscale ping 100.102.202.79` (server1) both answer, in 19-23ms. So the tailnet policy is **not** the blocker and neither is DERP; the daemon has a working path to the thing this watcher polls.
- `ip -o link show` lists no `tailscale0`, and `ip route get 100.89.73.98` resolves out `ovs_eth0` via the home gateway `192.168.68.1`. There is no route for `100.64.0.0/10` on the host, so a normal socket goes to the LAN and dies.
- `curl --max-time 12 https://100.89.73.98/` and the same against agora, grafana and `server1:22` all return code `000`. Four peers, one result: this is the host, not one ACL on one node.
- `curl https://nova.tailc83eb3.ts.net/...` fails in **0.06s**, not on a timeout: the NAS resolver is the home router `192.168.1.1` and it answers `NXDOMAIN`. `tailscale debug prefs` says `CorpDNS: true`, so MagicDNS is switched on in the daemon and simply is not in the host's `resolv.conf`. The DNS failure is a second, independent problem from the routing one — fixing MagicDNS alone changes nothing.
- `tailscale nc 100.89.73.98 80` **does** connect and returns a real `connection refused` from nova. A refusal is a round trip: the daemon can carry traffic the host cannot.

The cause is in Synology's own package. `/var/packages/Tailscale/scripts/start-stop-status` appends `--tun=userspace-networking` when `/dev/net/tun` is absent at start, and in userspace mode tailscaled creates no interface and installs no routes — it is reachable *inbound* (which is how this loop's own SSH arrives) and offers nothing outbound to other processes. `/dev/net/tun` exists on the box now, so a restart may well come up with a real tun device; the running daemon was started when it did not.

**Two candidate fixes, and the recommendation is the first.**

1. **Restart the Tailscale package** so it re-evaluates `/dev/net/tun` and comes up with a `tailscale0` interface and real routes. One command, and it fixes the routing and the DNS together. It is not a cycle's call to make unattended: this loop reaches the NAS *over the tailnet*, so a restart that does not come back cleanly cuts the only remote path into that box until someone is standing in front of it.
2. **Run tailscaled with `--socks5-server`** and give the container a SOCKS proxy. That leaves the host with no tailnet route, so it fixes this one program and nothing else, and it still needs a daemon restart to add the flag. Strictly worse than (1) for the same risk.

Until one of those happens, `NOVA_WATCH_URL` has no value that works from that box, and the two secrets below are not the thing standing in the way.

## Running it

See the header of `Dockerfile`. Docker on DSM is enough — no k3s, no cluster join. A k3s agent at home would be worse than nothing here: its API server is `server1`, so a worker bought with the exact failure we are trying to survive still schedules nothing.

## Testing it

`python3 -m pytest tests/test_nova_watch.py` in the runner repo. The whole decision path — the grace, both dedupe keys, the recovery, the refusal to record a failed send — is a pure function of an observation and the state between polls, so it runs with no socket, no NAS and no push subscription.
