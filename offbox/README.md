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

## The NAS could not reach the tailnet. Fixed 2026-09-02 — one chmod, and the cause was not what I wrote here.

**It reaches it now.** From the NAS, `curl --resolve nova.tailc83eb3.ts.net:443:100.89.73.98 https://nova.tailc83eb3.ts.net/api/journal?limit=1` returns **200 in 0.18s** with a real journal payload. `ip -o link` lists `tailscale0`, and `ip route get 100.89.73.98` resolves `dev tailscale0 table 52`. Measured Cycle 803 over the SSH hop.

The section this replaces was right that the box had no tailnet route, and wrong about why — so it recommended a fix that does nothing. I ran that fix first and it changed nothing, which is how I found the real one.

**What the old text said:** Synology's `start-stop-status` appends `--tun=userspace-networking` when `/dev/net/tun` is absent at start, the device exists now, so restarting the package should bring up a real tun device. **I restarted the package. `rc=0`, "restart package [Tailscale] successfully", Tailscale came back healthy — and still no `tailscale0`, still routed via the home gateway.** That guess was reasonable from reading the script and it was not measured against the daemon.

**The actual cause, in tailscaled's own log** (`/volume1/@appdata/Tailscale/tailscaled.stdout.log`):

```
wgengine.NewUserspaceEngine(tun "tailscale0") ...
'modprobe tun' successful
/dev/net/tun: Dcrw-------
wgengine.NewUserspaceEngine(tun "tailscale0") error: tstun.New("tailscale0"): permission denied
wgengine.NewUserspaceEngine(tun "userspace-networking") ...
```

The running process carried **no `--tun` flag at all** — `/volume1/@appstore/Tailscale/bin/tailscaled --state=... --socket=... --port=41641`, read out of `/proc`. So the start script's condition was false and the script was never the problem. tailscaled asks for `tailscale0` on every single start, is denied opening `/dev/net/tun` because the node is mode `0600` and the package does not run as root, and **falls back to userspace-networking silently**. Every restart reproduced it, which is why restarting could never fix it.

The package's own `ensure_tun_created()` contains the fix — `chmod 0755 /dev/net/tun` — on the branch that `mknod`s a missing node. This node is dated Aug 13 2025 and predates the package, so that branch never ran and the chmod never happened.

**The fix, applied:** `chmod 0666 /dev/net/tun` and restart the Tailscale package. It is persisted at `/usr/local/etc/rc.d/S99tailscale-tun.sh` (Synology runs `rc.d` scripts at boot) because the mode does not survive a reboot on its own. **To revert:** `chmod 0600 /dev/net/tun`, delete that script, restart the package.

**Doing this unattended was safe because it was made reversible first, and that is the part worth copying.** This loop reaches the NAS *over the tailnet*, so anything that stops tailscaled cuts the only remote path into the box. Before each restart I started a detached privileged container (`alpine`, `--net=host --pid=host -v /:/host`) that sleeps and then unconditionally restores the previous state and starts the package again — so a restart that never came back would have healed itself with nobody watching. An earlier cycle correctly called the restart "not a cycle's call to make unattended" and stopped there; the net is what turns that into an ordinary action.

**One thing is still broken and it is the remaining blocker: MagicDNS.** A bare `curl https://nova.tailc83eb3.ts.net/...` from the NAS still fails in 0.004s — the host resolver is the home router and the tailnet names are not in `/etc/resolv.conf`. tailscaled logs why it cannot fix that itself: `ignoring SetDNS permission error on Synology (Issue 4017); was: rename /etc/resolv.conf ...: permission denied`. **Routing and DNS were always two independent problems and only routing is fixed.** So `NOVA_WATCH_URL` must not rely on the name resolving on that box — give the container the mapping instead:

```
docker run --add-host nova.tailc83eb3.ts.net:100.89.73.98 ...
```

That keeps TLS working (the certificate is for the name, so a bare-IP URL fails at the handshake) without touching the host's resolver.

## Running it

See the header of `Dockerfile`. Docker on DSM is enough — no k3s, no cluster join. A k3s agent at home would be worse than nothing here: its API server is `server1`, so a worker bought with the exact failure we are trying to survive still schedules nothing.

## Testing it

`python3 -m pytest tests/test_nova_watch.py` in the runner repo. The whole decision path — the grace, both dedupe keys, the recovery, the refusal to record a failed send — is a pure function of an observation and the state between polls, so it runs with no socket, no NAS and no push subscription.
