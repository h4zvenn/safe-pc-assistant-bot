from __future__ import annotations

import ctypes
import datetime as dt
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.paths import expand_path
from utils.yaml_loader import load_yaml

_DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_KEYEVENTF_KEYUP = 0x0002
_VK_LWIN, _VK_D, _VK_SNAPSHOT = 0x5B, 0x44, 0x2C


def current_time(now: dt.datetime | None = None) -> tuple[str, dict[str, str]]:
    now = now or dt.datetime.now()
    hm = now.strftime("%H:%M")
    date = f"{_DAYS[now.weekday()]}, {now.day} {_MONTHS[now.month - 1]}"
    return f"Сейчас {hm}, {date}.", {"hm": hm, "date": date}


def _press(*codes: int) -> None:
    """Press a fixed key combination (the codes are constants in this module, never user input)."""
    user32 = ctypes.windll.user32
    for code in codes:
        user32.keybd_event(code, 0, 0, 0)
    for code in reversed(codes):
        user32.keybd_event(code, 0, _KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)


def lock_pc() -> tuple[bool, str]:
    if not ctypes.windll.user32.LockWorkStation():
        return False, "Не удалось заблокировать экран."
    return True, "Экран заблокирован."


def show_desktop() -> tuple[bool, str]:
    _press(_VK_LWIN, _VK_D)
    return True, "Окна свёрнуты (Win+D). Повтори, чтобы вернуть."


def take_screenshot() -> tuple[bool, str]:
    _press(_VK_LWIN, _VK_SNAPSHOT)
    return True, "Скриншот сохранён в «Изображения → Снимки экрана»."


@dataclass(slots=True)
class FolderShortcut:
    key: str
    name: str
    path: str
    aliases: list[str] = field(default_factory=list)


def load_folder_shortcuts(data_dir: Path) -> dict[str, FolderShortcut]:
    block = (load_yaml(data_dir / "folders.yaml").get("shortcuts")) or {}
    result: dict[str, FolderShortcut] = {}
    for key, value in block.items() if isinstance(block, dict) else []:
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            continue
        aliases: Any = value.get("aliases") or []
        result[str(key).lower()] = FolderShortcut(
            key=str(key).lower(),
            name=str(value.get("name") or key),
            path=value["path"],
            aliases=[str(a) for a in aliases] if isinstance(aliases, list) else [],
        )
    return result


def open_folder(data_dir: Path, folder_key: str) -> tuple[bool, str]:
    folder = load_folder_shortcuts(data_dir).get(folder_key)
    if folder is None:
        return False, f"Папка «{folder_key}» не зарегистрирована в folders.yaml."
    path = expand_path(folder.path)
    if not path.is_dir():
        return False, f"Папка «{folder.name}» не найдена: {path}"
    os.startfile(str(path))  # noqa: S606 - a directory from the config: opens in Explorer
    return True, f"Папка «{folder.name}» открыта."
