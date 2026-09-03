FROM python:3.12-slim
# openssh-client is here for issue #122 -- the k3s bootstrap on the home NAS runs
# over SSH from this pod, and terminal_exec had no ssh binary at all (measured
# Cycle 572: `which ssh` is empty in both this pod and the bridge pod).
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/*

# kubectl + gh CLI -- used by kubectl_read/github_read/create_pr/merge_pr/
# terminal_exec (Agora Issues.md #3 and #1). Pinned versions, not "latest",
# so a rebuild months from now doesn't silently pick up a different major
# version. Same pattern as the vault-bridge image this service used to
# borrow before it had its own repo/image (2026-07-29 migration).
ARG KUBECTL_VERSION=v1.35.8
RUN curl -fsSLo /usr/local/bin/kubectl \
    "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl

ARG GH_CLI_VERSION=2.98.0
RUN curl -fsSL \
    "https://github.com/cli/cli/releases/download/v${GH_CLI_VERSION}/gh_${GH_CLI_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C /usr/local --strip-components=1 "gh_${GH_CLI_VERSION}_linux_amd64/bin/gh"

WORKDIR /app
# agora_runner stopped being stdlib-only when tools_kubectl_test.py (#660)
# started parsing manifests with `import yaml`. PR that broke this shipped
# without noticing the image had no pip install step at all -- the container
# crashed on startup with ModuleNotFoundError, taking down the heartbeat.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agora_runner/ agora_runner/
# Two entrypoints out of one image: run.py is the runner (poll loop,
# heartbeats, /invoke), run_nova_site.py is Nova's site. They deploy as
# separate pods with different lifecycles, but share this image so the
# vault client stays a single copy -- see agora_runner/nova_site_main.py.
# The CMD below is the runner; the nova-site Deployment overrides `command`.
COPY run.py run_nova_site.py ./

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin runner
USER runner

CMD ["python", "run.py"]
