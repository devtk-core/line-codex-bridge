from __future__ import annotations

import base64
import hashlib
import hmac
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_line.config import Config
from codex_line.line_api import truncate_text, verify_signature
from codex_line.runner import CodexRunner
from codex_line.server import Bridge


class SignatureTests(unittest.TestCase):
    def test_valid_signature(self) -> None:
        body = b'{"events":[]}'
        secret = "test-secret"
        signature = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        self.assertTrue(verify_signature(body, signature, secret))
        self.assertFalse(verify_signature(body + b" ", signature, secret))

    def test_truncate_text(self) -> None:
        self.assertEqual(truncate_text("abc", 10), "abc")
        self.assertLessEqual(len(truncate_text("x" * 100, 50)), 50)


class ConfigTests(unittest.TestCase):
    def test_allowlist_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "LINE_CHANNEL_SECRET": "secret",
                "LINE_CHANNEL_ACCESS_TOKEN": "token",
                "CODEX_WORKDIR": directory,
                "LINE_ALLOWED_USER_IDS": "",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "LINE_ALLOWED_USER_IDS"):
                Config.from_env()


class RunnerTests(unittest.TestCase):
    def test_runner_uses_stdin_without_shell_and_hides_line_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HOME": "/home/test",
                "PATH": "/usr/bin",
                "LINE_CHANNEL_SECRET": "must-not-leak",
            },
            clear=True,
        ), patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "done\n"
            run.return_value.stderr = ""
            runner = CodexRunner("/usr/bin/codex", Path(directory), 60)
            result = runner.run("inspect this")

            self.assertTrue(result.ok)
            args, kwargs = run.call_args
            self.assertIsInstance(args[0], list)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["input"], "inspect this")
            self.assertNotIn("LINE_CHANNEL_SECRET", kwargs["env"])
            self.assertEqual(kwargs["env"]["HOME"], "/home/test")


class _InlineExecutor:
    def submit(self, function, *args):  # type: ignore[no-untyped-def]
        function(*args)

    def shutdown(self, **_kwargs):  # type: ignore[no-untyped-def]
        return None


class _FakeLine:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str]] = []
        self.pushes: list[tuple[str, str]] = []

    def reply(self, token: str, text: str) -> None:
        self.replies.append((token, text))

    def push(self, destination: str, text: str) -> None:
        self.pushes.append((destination, text))


class _FakeRunner:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, prompt: str):  # type: ignore[no-untyped-def]
        from codex_line.runner import CodexResult

        self.prompts.append(prompt)
        return CodexResult(True, "finished")


class BridgeTests(unittest.TestCase):
    def _bridge(self, directory: str) -> Bridge:
        config = Config(
            channel_secret="secret",
            channel_access_token="token",
            allowed_user_ids=frozenset({"Uallowed"}),
            workdir=Path(directory),
            codex_bin="codex",
            host="127.0.0.1",
            port=8080,
            timeout_seconds=60,
            max_message_chars=100,
            command_prefix="/codex",
            allow_groups=False,
        )
        bridge = Bridge(config)
        bridge.line = _FakeLine()  # type: ignore[assignment]
        bridge.runner = _FakeRunner()  # type: ignore[assignment]
        bridge.executor.shutdown(wait=False, cancel_futures=True)
        bridge.executor = _InlineExecutor()  # type: ignore[assignment]
        return bridge

    @staticmethod
    def _event(user_id: str, text: str) -> dict[str, object]:
        return {
            "webhookEventId": f"event-{user_id}-{text}",
            "type": "message",
            "replyToken": "reply-token",
            "source": {"type": "user", "userId": user_id},
            "message": {"type": "text", "text": text},
        }

    def test_authorized_prefixed_message_runs_codex_and_pushes_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._bridge(directory)
            bridge.handle_event(self._event("Uallowed", "/codex inspect this"))
            self.assertEqual(bridge.runner.prompts, ["inspect this"])  # type: ignore[attr-defined]
            self.assertEqual(len(bridge.line.replies), 1)  # type: ignore[attr-defined]
            self.assertEqual(bridge.line.pushes[0][0], "Uallowed")  # type: ignore[attr-defined]

    def test_unauthorized_user_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._bridge(directory)
            bridge.handle_event(self._event("Uother", "/codex inspect this"))
            self.assertEqual(bridge.runner.prompts, [])  # type: ignore[attr-defined]
            self.assertIn("実行権限", bridge.line.replies[0][1])  # type: ignore[attr-defined]

    def test_message_without_prefix_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._bridge(directory)
            bridge.handle_event(self._event("Uallowed", "hello"))
            self.assertEqual(bridge.runner.prompts, [])  # type: ignore[attr-defined]
            self.assertEqual(bridge.line.replies, [])  # type: ignore[attr-defined]

    def test_group_message_is_rejected_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._bridge(directory)
            event = self._event("Uallowed", "/codex inspect this")
            event["source"] = {
                "type": "group",
                "groupId": "Gexample",
                "userId": "Uallowed",
            }
            bridge.handle_event(event)
            self.assertEqual(bridge.runner.prompts, [])  # type: ignore[attr-defined]
            self.assertIn("グループ", bridge.line.replies[0][1])  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
