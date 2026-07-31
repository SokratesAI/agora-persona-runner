# agora-persona-runner
Agora's persona-runner: turn engine, heartbeat/workflow scheduler, tool implementations. Real repo+image (2026-07-29), replacing an embedded ConfigMap script.

Stdlib-only at runtime (no `requirements.txt` needed) -- `python run.py` starts it. Deployed via the paired [agora-persona-runner-config](https://github.com/SokratesAI/agora-persona-runner-config) repo, auto-discovered by ArgoCD; CI here builds+pushes the image and commits the digest there on every merge to `main`.

## Development & Testing

- Run unit tests: `pip install pytest && pytest tests/`
- Run local server / turn runner manually (requires configuration environment variables): `python run.py`
