from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request


LINE_API_BASE = "https://api.line.me/v2/bot/message"
LINE_TEXT_LIMIT = 5000


def verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    expected = base64.b64encode(
        hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature)


def truncate_text(text: str, limit: int = LINE_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n\n…（LINEの文字数上限に合わせて省略しました）"
    return text[: limit - len(suffix)] + suffix


class LineClient:
    def __init__(self, access_token: str, timeout_seconds: int = 15) -> None:
        self._access_token = access_token
        self._timeout_seconds = timeout_seconds

    def reply(self, reply_token: str, text: str) -> None:
        self._post(
            f"{LINE_API_BASE}/reply",
            {"replyToken": reply_token, "messages": [self._text_message(text)]},
        )

    def push(self, destination: str, text: str) -> None:
        self._post(
            f"{LINE_API_BASE}/push",
            {"to": destination, "messages": [self._text_message(text)]},
        )

    @staticmethod
    def _text_message(text: str) -> dict[str, str]:
        return {"type": "text", "text": truncate_text(text)}

    def _post(self, url: str, payload: dict[str, object]) -> None:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds):
                return
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", errors="replace")
            raise RuntimeError(f"LINE API returned HTTP {exc.code}: {detail}") from exc
