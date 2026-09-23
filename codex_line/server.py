from __future__ import annotations

import json
import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Config
from .line_api import LineClient, verify_signature
from .runner import CodexRunner


LOG = logging.getLogger("codex-line")


def _destination(source: dict[str, Any]) -> str | None:
    source_type = source.get("type")
    if source_type == "user":
        return source.get("userId")
    if source_type == "group":
        return source.get("groupId")
    if source_type == "room":
        return source.get("roomId")
    return None


class Bridge:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.line = LineClient(config.channel_access_token)
        self.runner = CodexRunner(
            config.codex_bin, config.workdir, config.timeout_seconds
        )
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="codex-job")
        self._seen: dict[str, float] = {}
        self._seen_lock = threading.Lock()

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def handle_event(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("webhookEventId", ""))
        if event_id and self._is_duplicate(event_id):
            LOG.info("Ignoring duplicate webhook event %s", event_id)
            return

        if event.get("type") != "message":
            return
        message = event.get("message") or {}
        if message.get("type") != "text":
            self._safe_reply(event.get("replyToken"), "テキストメッセージを送ってください。")
            return

        source = event.get("source") or {}
        if source.get("type") in {"group", "room"} and not self.config.allow_groups:
            self._safe_reply(
                event.get("replyToken"),
                "安全のため、グループ／ルームからの実行は無効です。",
            )
            return
        user_id = source.get("userId")
        if not user_id or user_id not in self.config.allowed_user_ids:
            LOG.warning("Rejected an event from an unauthorized LINE user")
            self._safe_reply(event.get("replyToken"), "このユーザーには実行権限がありません。")
            return

        prompt = str(message.get("text", "")).strip()
        prefix = self.config.command_prefix
        if prefix:
            if not prompt.startswith(prefix):
                return
            prompt = prompt[len(prefix) :].strip()
        if not prompt:
            self._safe_reply(event.get("replyToken"), "Codexへの指示を入力してください。")
            return
        if len(prompt) > self.config.max_message_chars:
            self._safe_reply(
                event.get("replyToken"),
                f"指示が長すぎます（上限 {self.config.max_message_chars} 文字）。",
            )
            return

        destination = _destination(source)
        if not destination:
            self._safe_reply(event.get("replyToken"), "返信先を特定できませんでした。")
            return

        self._safe_reply(event.get("replyToken"), "受け付けました。Codexを実行します。")
        self.executor.submit(self._run_and_notify, destination, prompt, event_id)

    def _run_and_notify(self, destination: str, prompt: str, event_id: str) -> None:
        LOG.info("Starting Codex job %s", event_id or "(no event id)")
        result = self.runner.run(prompt)
        heading = "Codex完了" if result.ok else "Codexエラー"
        try:
            self.line.push(destination, f"{heading}\n\n{result.output}")
        except Exception:
            LOG.exception("Could not send the Codex result to LINE")
        LOG.info("Finished Codex job %s (ok=%s)", event_id or "(no event id)", result.ok)

    def _safe_reply(self, reply_token: Any, text: str) -> None:
        if not isinstance(reply_token, str) or not reply_token:
            return
        try:
            self.line.reply(reply_token, text)
        except Exception:
            LOG.exception("Could not reply to LINE")

    def _is_duplicate(self, event_id: str) -> bool:
        now = time.monotonic()
        cutoff = now - 3600
        with self._seen_lock:
            self._seen = {
                key: timestamp
                for key, timestamp in self._seen.items()
                if timestamp >= cutoff
            }
            if event_id in self._seen:
                return True
            self._seen[event_id] = now
            return False


class WebhookHandler(BaseHTTPRequestHandler):
    bridge: Bridge

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(HTTPStatus.OK, b'{"status":"ok"}', "application/json")
            return
        self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/webhook":
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, b"invalid content length", "text/plain")
            return
        if length <= 0 or length > 1_000_000:
            self._send(HTTPStatus.BAD_REQUEST, b"invalid body size", "text/plain")
            return

        body = self.rfile.read(length)
        signature = self.headers.get("X-Line-Signature", "")
        if not verify_signature(body, signature, self.bridge.config.channel_secret):
            self._send(HTTPStatus.UNAUTHORIZED, b"invalid signature", "text/plain")
            return
        try:
            payload = json.loads(body)
            events = payload.get("events", [])
            if not isinstance(events, list):
                raise ValueError("events must be a list")
        except (json.JSONDecodeError, ValueError, AttributeError):
            self._send(HTTPStatus.BAD_REQUEST, b"invalid payload", "text/plain")
            return

        # Acknowledge before Codex starts. Work continues in the single-job executor.
        self._send(HTTPStatus.OK, b"ok", "text/plain")
        for event in events:
            if isinstance(event, dict):
                self.bridge.handle_event(event)

    def log_message(self, format: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), format % args)

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = Config.from_env()
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    bridge = Bridge(config)
    WebhookHandler.bridge = bridge
    server = ThreadingHTTPServer((config.host, config.port), WebhookHandler)
    server.daemon_threads = True

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info("Listening on http://%s:%d", config.host, config.port)
    try:
        server.serve_forever()
    finally:
        bridge.close()
        server.server_close()


if __name__ == "__main__":
    main()
