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
| `NOVA_WATCH_URL` | optional; defaults to the tailnet journal endpoint. |
| `NOVA_WATCH_INTERVAL` | optional; seconds between polls, default 300. |

It refuses to start without the first two rather than running as a watchdog that cannot speak. A silent watchdog is worse than none, because it looks like coverage.

**Both of those values still have to be handed over by a human.** Reading a Kubernetes Secret and `exec`ing into a pod are both refused for a Nova cycle, at the tool layer and at RBAC. That is step 1 of the design — prove one real send from a machine that is not `server1` — and it is the only step here that a cycle cannot do alone. Copying the VAPID private key to the NAS widens where that key lives, which is a real trade and the owner's to refuse; the alternative is an alarm that cannot ring during the outage it exists for.

## Running it

See the header of `Dockerfile`. Docker on DSM is enough — no k3s, no cluster join. A k3s agent at home would be worse than nothing here: its API server is `server1`, so a worker bought with the exact failure we are trying to survive still schedules nothing.

## Testing it

`python3 -m pytest tests/test_nova_watch.py` in the runner repo. The whole decision path — the grace, both dedupe keys, the recovery, the refusal to record a failed send — is a pure function of an observation and the state between polls, so it runs with no socket, no NAS and no push subscription.
