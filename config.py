from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from utils.paths import PROJECT_ROOT, expand_path

load_dotenv(PROJECT_ROOT / ".env")

_TOKEN_FORMAT = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


def _parse_int_list(name: str, value: str) -> list[int]:
    result: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError as exc:
            raise RuntimeError(f"{name} must be comma-separated integers, got: {part!r}") from exc
    return result


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, default: str, minimum: int = 0) -> int:
    raw = os.getenv(name, default).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got: {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got: {value}")
    return value


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    path = expand_path(raw)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


@dataclass(slots=True)
class AppConfig:
    bot_token: str = ""
    bot_name: str = "ПК-ассистент"
    authorized_user_ids: list[int] = field(default_factory=list)
    authorized_chat_ids: list[int] = field(default_factory=list)
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")
    alerts_enabled: bool = True
    default_parse_mode: str = "HTML"
    rate_limit_commands_per_minute: int = 20
    confirmation_ttl_seconds: int = 30
    local_confirmation_ttl_seconds: int = 15
    shutdown_delay_seconds: int = 30
    ping_host: str = "8.8.8.8"
    # Reserved for a future file-download feature; not used by any handler yet.
    download_timeout_seconds: int = 60
    max_download_size_mb: int = 100

    @classmethod
    def from_env(cls, require_telegram: bool = True) -> "AppConfig":
        """Build config from the environment.

        The local JARVIS modes pass require_telegram=False so they run without a bot token.
        """
        token = os.getenv("BOT_TOKEN", "").strip()
        user_ids = _parse_int_list("AUTHORIZED_USER_IDS", os.getenv("AUTHORIZED_USER_IDS", ""))
        chat_ids = _parse_int_list("AUTHORIZED_CHAT_IDS", os.getenv("AUTHORIZED_CHAT_IDS", ""))

        parse_mode = (os.getenv("DEFAULT_PARSE_MODE", "HTML").strip() or "HTML").upper()
        if parse_mode != "HTML":
            # Every handler builds HTML markup; MarkdownV2 would break all messages.
            raise RuntimeError("DEFAULT_PARSE_MODE must be HTML (the only supported mode)")

        config = cls(
            bot_token=token,
            bot_name=os.getenv("BOT_NAME", "ПК-ассистент").strip() or "ПК-ассистент",
            authorized_user_ids=user_ids,
            authorized_chat_ids=chat_ids,
            data_dir=_env_path("DATA_DIR", PROJECT_ROOT / "data"),
            log_dir=_env_path("LOG_DIR", PROJECT_ROOT / "logs"),
            alerts_enabled=_parse_bool(os.getenv("ALERTS_ENABLED"), True),
            default_parse_mode=parse_mode,
            rate_limit_commands_per_minute=_parse_int_env("RATE_LIMIT_COMMANDS_PER_MINUTE", "20", 1),
            confirmation_ttl_seconds=_parse_int_env("CONFIRMATION_TTL_SECONDS", "30", 5),
            local_confirmation_ttl_seconds=_parse_int_env("LOCAL_CONFIRMATION_TTL_SECONDS", "15", 5),
            shutdown_delay_seconds=_parse_int_env("SHUTDOWN_DELAY_SECONDS", "30", 5),
            ping_host=os.getenv("PING_HOST", "8.8.8.8").strip() or "8.8.8.8",
            download_timeout_seconds=_parse_int_env("DOWNLOAD_TIMEOUT_SECONDS", "60", 1),
            max_download_size_mb=_parse_int_env("MAX_DOWNLOAD_SIZE_MB", "100", 1),
        )

        if require_telegram:
            if not token or token.startswith("123456:replace"):
                raise RuntimeError("BOT_TOKEN is missing in .env (see .env.example)")
            if not _TOKEN_FORMAT.match(token):
                # Never echo the value: it may be a real secret with a typo.
                raise RuntimeError(
                    "BOT_TOKEN has the wrong format: expected <digits>:<35 letters/digits> "
                    "exactly as BotFather prints it, with no extra prefix, quotes or spaces"
                )
            if not user_ids and not chat_ids:
                raise RuntimeError("Set AUTHORIZED_USER_IDS in .env (see .env.example)")

        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.log_dir.mkdir(parents=True, exist_ok=True)
        return config
