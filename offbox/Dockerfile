# The off-box watcher, built to run on the owner's Synology NAS under DSM's
# Container Manager -- not in this cluster. Anything that runs on server1
# cannot watch server1; that is the whole design (idea #113, issue #103).
#
# Build and run at home:
#   docker build -t nova-watch offbox/
#   docker run -d --restart unless-stopped --name nova-watch \
#     -e NOVA_WATCH_SUBSCRIPTION="$(cat subscription.json)" \
#     -e VAPID_PRIVATE_KEY=... -e VAPID_PUBLIC_KEY=... \
#     nova-watch
#
# The NAS also needs Tailscale, so NOVA_WATCH_URL can reach the tailnet.
FROM python:3.12-slim

# pywebpush is the only dependency, and it is only needed to send. The
# decision logic imports nothing outside the standard library on purpose,
# so it stays testable in the runner's own test suite.
RUN pip install --no-cache-dir pywebpush==2.0.3

WORKDIR /app
COPY nova_watch.py /app/nova_watch.py

# Unbuffered, because every useful line this prints is printed during an
# outage and a buffered log is one nobody reads in time.
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/app/nova_watch.py"]
