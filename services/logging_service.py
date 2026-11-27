from __future__ import annotations

import logging
import re
from pathlib import Path

# Telegram bot tokens look like 123456789:AAH... — never let one reach a log file.
# No leading \b: the token sits right after "bot" in API URLs (".../bot123456:AAH.../getMe").
_TOKEN_PATTERN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _TOKEN_PATTERN.sub("<redacted-token>", super().format(record))


def setup_logging(log_dir: Path, console: bool = True) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "bot.log"
    formatter = RedactingFormatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if root_logger.handlers:
        root_logger.handlers.clear()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)
