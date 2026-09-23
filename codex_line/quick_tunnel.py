from __future__ import annotations

import logging
import os
import re
import select
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import TextIO

from .config import Config
from .line_api import LineClient


LOG = logging.getLogger("codex-line-tunnel")
QUICK_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def extract_quick_tunnel_url(line: str) -> str | None:
    match = QUICK_TUNNEL_URL.search(line)
    return match.group(0) if match else None


def _cloudflared_environment() -> dict[str, str]:
    # Do not pass LINE credentials to the public tunnel process.
    allowed = ("HOME", "PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
    return {name: os.environ[name] for name in allowed if name in os.environ}


def _wait_for_local_server(url: str, timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError(f"Local bridge did not become healthy: {url}")


def _wait_for_tunnel_url(stream: TextIO, timeout_seconds: int = 60) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([stream], [], [], 1)
        if not ready:
            continue
        line = stream.readline()
        if not line:
            raise RuntimeError("cloudflared exited before publishing a URL")
        LOG.info("cloudflared: %s", line.rstrip())
        url = extract_quick_tunnel_url(line)
        if url:
            return url
    raise RuntimeError("Timed out waiting for a trycloudflare.com URL")


def _register_line_webhook(
    client: LineClient,
    base_url: str,
    attempts: int = 24,
    delay_seconds: int = 5,
) -> dict[str, object]:
    endpoint = f"{base_url}/webhook"
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            client.set_webhook_endpoint(endpoint)
            test_result = client.test_webhook_endpoint()
            if test_result.get("success") is not True:
                raise RuntimeError(f"LINE webhook test failed: {test_result}")
            status = client.get_webhook_endpoint()
            LOG.info("LINE webhook updated to %s", endpoint)
            if status.get("active") is not True:
                LOG.warning(
                    "LINE webhook URL is valid but 'Use webhook' is disabled in the LINE console"
                )
            return status
        except Exception as exc:  # Retry while the new random DNS name warms up.
            last_error = exc
            LOG.warning("Webhook registration attempt %d failed: %s", attempt, exc)
            time.sleep(delay_seconds)
    raise RuntimeError("Could not register the Quick Tunnel URL with LINE") from last_error


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = Config.from_env()
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    cloudflared_bin = os.environ.get("CLOUDFLARED_BIN", "/usr/bin/cloudflared")
    if not Path(cloudflared_bin).is_file():
        raise SystemExit(f"cloudflared not found: {cloudflared_bin}")

    origin = f"http://{config.host}:{config.port}"
    _wait_for_local_server(f"{origin}/healthz")
    process = subprocess.Popen(
        [cloudflared_bin, "tunnel", "--no-autoupdate", "--url", origin],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=_cloudflared_environment(),
    )

    def stop(_signum: int, _frame: object) -> None:
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    if process.stdout is None:
        process.terminate()
        raise SystemExit("Could not capture cloudflared output")

    try:
        base_url = _wait_for_tunnel_url(process.stdout)
        client = LineClient(config.channel_access_token)
        _register_line_webhook(client, base_url)
        for line in process.stdout:
            LOG.info("cloudflared: %s", line.rstrip())
        return_code = process.wait()
        if return_code != 0:
            raise SystemExit(return_code)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
