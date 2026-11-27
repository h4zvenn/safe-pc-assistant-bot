from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from utils.paths import expand_path
from utils.yaml_loader import load_yaml

_EXE_NAME = re.compile(r"^[\w.\- ]{1,100}\.exe$", re.IGNORECASE)

# Detach launched apps from our console so they survive a restart of the assistant.
_DETACHED = (
    getattr(subprocess, "DETACHED_PROCESS", 0)
    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
)


@dataclass(slots=True)
class AppEntry:
    key: str
    display_name: str
    paths: list[str]
    args: list[str]
    process_names: list[str]
    allow_close: bool
    aliases: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.paths[0] if self.paths else ""


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def load_apps(data_dir: Path) -> dict[str, AppEntry]:
    data = load_yaml(data_dir / "apps.yaml")
    result: dict[str, AppEntry] = {}

    for key, value in (data.get("apps") or {}).items():
        if not isinstance(value, dict):
            continue
        key = str(key).lower()
        paths = _str_list(value.get("paths")) or _str_list(value.get("path"))
        result[key] = AppEntry(
            key=key,
            display_name=str(value.get("display_name") or value.get("name") or key),
            paths=paths,
            args=_str_list(value.get("args")),
            process_names=_str_list(value.get("process_names")),
            allow_close=bool(value.get("allow_close", False)),
            aliases=_str_list(value.get("aliases")),
        )

    return result


def resolve_app_path(app: AppEntry) -> Path | None:
    for raw in app.paths:
        candidate = expand_path(raw)
        if candidate.is_file():
            return candidate
    return None


def open_allowed_app(data_dir: Path, app_key: str) -> tuple[bool, str]:
    app = load_apps(data_dir).get(app_key)

    if not app:
        return False, f"Приложение «{app_key}» не зарегистрировано в apps.yaml."

    exe = resolve_app_path(app)
    if exe is None:
        return False, (
            f"{app.display_name} не найден на этом ПК. Установите его или поправьте "
            f"пути в data/apps.yaml (app_id: {app.key})."
        )

    try:
        subprocess.Popen(
            [str(exe), *app.args],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=_DETACHED,
        )
        return True, f"{app.display_name} открыт."
    except OSError as exc:
        return False, f"Ошибка запуска {app.display_name}: {exc}"


def close_allowed_app(data_dir: Path, app_key: str) -> tuple[bool, str]:
    app = load_apps(data_dir).get(app_key)

    if not app:
        return False, f"Приложение «{app_key}» не зарегистрировано в apps.yaml."
    if not app.allow_close:
        return False, f"{app.display_name}: закрытие не разрешено (allow_close в apps.yaml)."

    closed = False
    for name in app.process_names:
        if not _EXE_NAME.match(name):
            continue
        result = subprocess.run(
            ["taskkill", "/f", "/t", "/im", name],
            capture_output=True,
            check=False,
            shell=False,
            timeout=15,
        )
        closed = closed or result.returncode == 0

    if closed:
        return True, f"{app.display_name} закрыт."
    return False, f"{app.display_name} сейчас не запущен."


def running_allowlisted_apps(data_dir: Path) -> list[str]:
    apps = load_apps(data_dir)
    names: dict[str, str] = {}

    for app in apps.values():
        for proc in app.process_names:
            names[proc.lower()] = app.display_name

    found: set[str] = set()

    for proc in psutil.process_iter(["name"]):
        try:
            process_name = (proc.info.get("name") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        if process_name in names:
            found.add(names[process_name])

    return sorted(found)
