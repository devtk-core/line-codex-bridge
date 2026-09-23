from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodexResult:
    ok: bool
    output: str


class CodexRunner:
    def __init__(
        self,
        codex_bin: str,
        workdir: Path,
        timeout_seconds: int,
        max_output_chars: int = 20_000,
    ) -> None:
        self._codex_bin = codex_bin
        self._workdir = workdir
        self._timeout_seconds = timeout_seconds
        self._max_output_chars = max_output_chars

    def run(self, prompt: str) -> CodexResult:
        command = [
            self._codex_bin,
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--color",
            "never",
            "-C",
            str(self._workdir),
            "-",
        ]
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self._timeout_seconds,
                env=self._sanitized_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CodexResult(False, "Codexの実行がタイムアウトしました。")
        except OSError as exc:
            return CodexResult(False, f"Codexを起動できませんでした: {exc}")

        output = completed.stdout.strip()
        if completed.returncode != 0:
            detail = completed.stderr.strip()
            if len(detail) > 2000:
                detail = detail[-2000:]
            return CodexResult(
                False,
                "Codexの実行に失敗しました。"
                + (f"\n\n{detail}" if detail else ""),
            )
        if not output:
            output = "Codexは正常終了しましたが、返信テキストはありませんでした。"
        if len(output) > self._max_output_chars:
            output = output[: self._max_output_chars] + "\n\n…（出力を省略しました）"
        return CodexResult(True, output)

    @staticmethod
    def _sanitized_environment() -> dict[str, str]:
        # Do not expose LINE credentials (or unrelated service secrets) to commands
        # launched by Codex. HOME/CODEX_HOME are retained for existing Codex auth.
        allowed = (
            "HOME",
            "CODEX_HOME",
            "PATH",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        )
        return {name: os.environ[name] for name in allowed if name in os.environ}
