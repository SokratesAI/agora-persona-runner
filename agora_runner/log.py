"""Two logging helpers -- log() always prints, debug_log() only under DEBUG_LOGGING."""

from agora_runner.config import DEBUG_LOGGING


def log(msg):
    print(msg, flush=True)


def debug_log(msg):
    """Verbose-only diagnostics -- see DEBUG_LOGGING above for what this
    does and doesn't cover."""
    if DEBUG_LOGGING:
        print(f"[debug] {msg}", flush=True)
