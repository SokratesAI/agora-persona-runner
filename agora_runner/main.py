"""Entrypoint: starts the /invoke server, then polls forever."""

import time

from agora_runner.config import AGORA_URL, POLL_INTERVAL_SECONDS
from agora_runner.log import log
from agora_runner.poll import poll_once
from agora_runner.invoke_server import start_invoke_server


def main():
    start_invoke_server()
    log(f"polling {AGORA_URL}/conversations every {POLL_INTERVAL_SECONDS}s")
    while True:
        try:
            poll_once()
        except Exception as e:
            log(f"poll failed: {e}")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
