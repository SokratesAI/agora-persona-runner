# agora-persona-runner
Agora's persona-runner: turn engine, heartbeat/workflow scheduler, tool implementations. Real repo+image (2026-07-29), replacing an embedded ConfigMap script.

Stdlib-only at runtime (no `requirements.txt` needed) -- `python run.py` starts it. Deployed via the paired [agora-persona-runner-config](https://github.com/SokratesAI/agora-persona-runner-config) repo, auto-discovered by ArgoCD; CI here builds+pushes the image and commits the digest there on every merge to `main`.

## Local Development & Testing
To run tests locally:
```bash
pip install pytest
pytest tests/
```
To run the server locally (requires standard environment variables for Vault/API keys):
```bash
python run.py
```
