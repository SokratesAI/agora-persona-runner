# The off-box watcher, built to run on the owner's Synology NAS under DSM's
# Container Manager -- not in this cluster. Anything that runs on server1
# cannot watch server1; that is the whole design (idea #113, issue #103).
#
# Build and run at home. Telegram is the shorter path and the one he asked
# for in issue #259 -- one bot token, nothing copied off the cluster:
#   docker build -t nova-watch offbox/
#   docker run -d --restart unless-stopped --name nova-watch \
#     --add-host nova.tailc83eb3.ts.net:100.89.73.98 \
#     -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=... \
#     nova-watch
#
# Web Push is still supported and still needs two values copied off the
# cluster; it is chosen only when the two Telegram variables are absent:
#   docker run ... -e NOVA_WATCH_SUBSCRIPTION="$(cat subscription.json)" \
#     -e VAPID_PRIVATE_KEY=... nova-watch
#
# `--add-host` is not optional on the NAS: its tailnet route works but
# MagicDNS does not reach its resolver, so the name does not resolve there.
# See README.md.
#
# The NAS also needs Tailscale, and having the package installed is not
# enough: measured 2026-08-30, DSM runs tailscaled in userspace-networking
# mode, which serves inbound connections and installs no host route, so a
# container there cannot reach NOVA_WATCH_URL at all. `README.md` has the
# measurements and the fix; do not build this expecting it to poll until
# `ip route get <nova tailnet ip>` on that box names a tailscale interface.
FROM python:3.12-slim

# pywebpush is the only dependency, and it is only needed for the Web Push
# sender. The Telegram sender uses `urllib` alone, so an image that only ever
# sends Telegram carries this for nothing -- left in rather than split,
# because a second image is a second thing to keep current on a box I cannot
# build on. The
# decision logic imports nothing outside the standard library on purpose,
# so it stays testable in the runner's own test suite.
#
# 2.4.0 is the latest on PyPI as of 2026-08-27 and the pin is measured, not
# remembered: I installed it here and read `inspect.signature(webpush)`, which
# takes `subscription_info`, `data`, `vapid_private_key` and `vapid_claims` --
# exactly the four `web_push_sender` passes. I cannot build this image from a
# cycle, so the signature is the only proof available that the call shape is
# right, and it is worth more than a version number I recalled.
RUN pip install --no-cache-dir pywebpush==2.4.0

WORKDIR /app
COPY nova_watch.py /app/nova_watch.py

# Unbuffered, because every useful line this prints is printed during an
# outage and a buffered log is one nobody reads in time.
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/app/nova_watch.py"]
