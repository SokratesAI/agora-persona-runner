"""Does `redact()` mask every credential Kubernetes mounted on this pod?

    python3 -m tools.redact_coverage

The check itself lives in `agora_runner/redact_coverage.py`, and that is
the whole reason this file is two lines. A coverage check can only read
the environment of the process it runs in, so it has to be able to run in
**both** pods that hold a `redact()` -- and `tools/` is not in the runner
image. `agora_runner/` is. On the runner pod the same check is one call:

    cd /app && python3 -m agora_runner.redact_coverage

This wrapper exists so `preflight`'s roster, which reads `tools/`, can
still name it. Exit contract, docstring and reasoning are all over there.
"""

import sys

from agora_runner.redact_coverage import main

if __name__ == "__main__":
    sys.exit(main())
