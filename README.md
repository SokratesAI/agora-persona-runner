# agora-persona-runner
Agora's persona-runner: the backend turn engine, heartbeat/workflow scheduler, and every tool implementation (the thing that actually calls Anthropic/Gemini).

## Deployment
Deployed via the paired [agora-persona-runner-config](https://github.com/SokratesAI/agora-persona-runner-config) repo, auto-discovered by ArgoCD; CI here builds+pushes the image and commits the digest there on every merge to `main`.

## Local Development & Testing
While the production image is designed to be stdlib-only (no `requirements.txt` needed to run the engine itself), running the test suite requires the `pytest` dependency:

```bash
pip install pytest
pytest tests/
```

To start the runner:
```bash
python run.py
```
