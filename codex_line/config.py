from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class Config:
    channel_secret: str
    channel_access_token: str
    allowed_user_ids: frozenset[str]
    workdir: Path
    codex_bin: str
    host: str
    port: int
    timeout_seconds: int
    max_message_chars: int
    command_prefix: str
    allow_groups: bool

    @classmethod
    def from_env(cls) -> "Config":
        allowed = frozenset(
            item.strip()
            for item in os.environ.get("LINE_ALLOWED_USER_IDS", "").split(",")
            if item.strip()
        )
        if not allowed:
            raise ValueError(
                "LINE_ALLOWED_USER_IDS is required and must contain at least one user ID"
            )

        workdir = Path(_required("CODEX_WORKDIR")).expanduser().resolve()
        if not workdir.is_dir():
            raise ValueError(f"CODEX_WORKDIR is not a directory: {workdir}")

        return cls(
            channel_secret=_required("LINE_CHANNEL_SECRET"),
            channel_access_token=_required("LINE_CHANNEL_ACCESS_TOKEN"),
            allowed_user_ids=allowed,
            workdir=workdir,
            codex_bin=os.environ.get("CODEX_BIN", "codex").strip() or "codex",
            host=os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_positive_int("PORT", 8080),
            timeout_seconds=_positive_int("CODEX_TIMEOUT_SECONDS", 1800),
            max_message_chars=_positive_int("MAX_MESSAGE_CHARS", 4000),
            command_prefix=os.environ.get("COMMAND_PREFIX", "").strip(),
            allow_groups=_boolean("ALLOW_GROUPS", False),
        )
