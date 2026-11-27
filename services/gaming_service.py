from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from utils.yaml_loader import load_yaml


@dataclass(slots=True)
class GamingSnapshot:
    active_items: list[str]
    detected_game: str | None


@dataclass(slots=True)
class GameEntry:
    key: str
    display_name: str
    steam_app_id: int | None
    aliases: list[str] = field(default_factory=list)


def load_games(data_dir: Path) -> dict[str, GameEntry]:
    data = load_yaml(data_dir / "games.yaml")
    result: dict[str, GameEntry] = {}
    for key, item in (data.get("games") or {}).items():
        if not isinstance(item, dict):
            continue
        steam_id = item.get("steam_app_id")
        aliases = item.get("aliases") or []
        result[str(key).lower()] = GameEntry(
            key=str(key).lower(),
            display_name=str(item.get("display_name", key)),
            steam_app_id=steam_id if isinstance(steam_id, int) and not isinstance(steam_id, bool) else None,
            aliases=[str(a) for a in aliases] if isinstance(aliases, list) else [],
        )
    return result


def launch_game(data_dir: Path, game_key: str) -> tuple[bool, str]:
    game = load_games(data_dir).get(game_key)
    if not game:
        return False, f"Игра «{game_key}» не зарегистрирована в games.yaml."
    if game.steam_app_id is None:
        return False, f"{game.display_name}: запуск через Steam не настроен (steam_app_id в games.yaml)."
    try:
        # The URL is built from a validated integer, never from user text.
        os.startfile(f"steam://rungameid/{game.steam_app_id}")  # noqa: S606
    except OSError as exc:
        return False, f"Не удалось запустить {game.display_name} (установлен ли Steam?): {exc}"
    return True, f"Запуск {game.display_name} отправлен в Steam."


def load_game_map(data_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    data = load_yaml(data_dir / 'games.yaml')
    game_map: dict[str, str] = {}
    launcher_map: dict[str, str] = {}
    for item in (data.get('games') or {}).values():
        display_name = item.get('display_name', 'Unknown Game')
        for proc_name in item.get('process_names', []):
            game_map[proc_name.lower()] = display_name
    for item in (data.get('launchers') or {}).values():
        display_name = item.get('display_name', 'Unknown Launcher')
        for proc_name in item.get('process_names', []):
            launcher_map[proc_name.lower()] = display_name
    return game_map, launcher_map


def get_gaming_snapshot(data_dir: Path) -> GamingSnapshot:
    game_map, launcher_map = load_game_map(data_dir)
    active: list[str] = []
    detected_game: str | None = None
    for proc in psutil.process_iter(['name']):
        try:
            name = (proc.info.get('name') or '').lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name in game_map:
            active.append(game_map[name])
            if detected_game is None:
                detected_game = game_map[name]
        if name in launcher_map:
            active.append(launcher_map[name])
    return GamingSnapshot(active_items=sorted(set(active)), detected_game=detected_game)
