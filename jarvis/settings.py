from __future__ import annotations

import os
from dataclasses import dataclass, field

from config import _parse_bool, _parse_int_env  # same parsing rules as the bot config


def _parse_float_env(name: str, default: str) -> float:
    raw = os.getenv(name, default).strip() or default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got: {raw!r}") from exc
    if not 0 <= value < 1:
        raise RuntimeError(f"{name} must be between 0 and 1, got: {value}")
    return value


def _languages(raw: str) -> tuple[str, ...]:
    langs = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    allowed = tuple(lang for lang in langs if lang in ("ru", "uk"))
    return allowed or ("ru", "uk")


@dataclass(slots=True)
class JarvisSettings:
    """Voice / local-mode settings. All have safe defaults; see .env.example."""

    ptt_key: str = "F9"
    stt_model: str = "small"
    stt_device: str = "cpu"  # cpu | cuda | auto
    stt_compute_type: str = "int8"
    languages: tuple[str, ...] = ("ru", "uk")
    input_device: str = ""  # sounddevice index or name; empty = system default
    max_record_seconds: int = 15
    beeps: bool = True
    tts_enabled: bool = True
    listen_wake_word: bool = True
    listen_followup_seconds: int = 8
    listen_threshold: float = 0.0  # 0 = automatic (adapts to room noise)
    ollama_enabled: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:9b"
    ollama_timeout_seconds: int = 30
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "JarvisSettings":
        return cls(
            ptt_key=os.getenv("JARVIS_PTT_KEY", "F9").strip() or "F9",
            stt_model=os.getenv("JARVIS_STT_MODEL", "small").strip() or "small",
            stt_device=(os.getenv("JARVIS_STT_DEVICE", "cpu").strip() or "cpu").lower(),
            stt_compute_type=os.getenv("JARVIS_STT_COMPUTE_TYPE", "int8").strip() or "int8",
            languages=_languages(os.getenv("JARVIS_LANGUAGES", "ru,uk")),
            input_device=os.getenv("JARVIS_INPUT_DEVICE", "").strip(),
            max_record_seconds=_parse_int_env("JARVIS_MAX_RECORD_SECONDS", "15", 2),
            beeps=_parse_bool(os.getenv("JARVIS_BEEPS"), True),
            tts_enabled=_parse_bool(os.getenv("JARVIS_TTS_ENABLED"), True),
            listen_wake_word=_parse_bool(os.getenv("JARVIS_WAKE_WORD"), True),
            listen_followup_seconds=_parse_int_env("JARVIS_FOLLOWUP_SECONDS", "8", 0),
            listen_threshold=_parse_float_env("JARVIS_LISTEN_THRESHOLD", "0"),
            ollama_enabled=_parse_bool(os.getenv("JARVIS_OLLAMA_ENABLED"), True),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").strip(),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen3.5:9b").strip() or "qwen3.5:9b",
            ollama_timeout_seconds=_parse_int_env("OLLAMA_TIMEOUT_SECONDS", "30", 1),
        )
