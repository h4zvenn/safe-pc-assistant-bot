from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from actions import ActionService  # noqa: E402
from config import AppConfig  # noqa: E402

APPS_YAML = """
apps:
  steam:
    name: "Steam"
    paths: ["{exe}"]
    process_names: ["steam.exe"]
    aliases: ["steam", "стим"]
    allow_close: true
  discord:
    name: "Discord"
    paths: ["{exe}"]
    args: ["--processStart", "Discord.exe"]
    process_names: ["Discord.exe"]
    aliases: ["discord", "дискорд"]
    allow_close: true
  spotify:
    name: "Spotify"
    paths: ["{missing}"]
    process_names: ["Spotify.exe"]
    aliases: ["spotify", "спотифай"]
  notepad:
    name: "Notepad"
    paths: ["{exe}"]
    aliases: ["блокнот"]
"""

SCENARIOS_YAML = """
scenarios:
  gaming:
    description: "Gaming"
    aliases: ["игровой режим", "ігровий режим"]
    steps:
      - type: health_check
      - type: open_app
        app: steam
      - type: open_app
        app: discord
  work:
    description: "Work"
    aliases: ["рабочий режим"]
    require_confirmation: true
    steps:
      - type: open_app
        app: notepad
"""

GAMES_YAML = """
games:
  cs2:
    display_name: "CS2"
    steam_app_id: 730
    aliases: ["cs2", "кс2"]
    process_names: ["cs2.exe"]
launchers: {}
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    exe = Path(sys.executable).as_posix()
    data = tmp_path / "data"
    data.mkdir()
    (data / "apps.yaml").write_text(
        APPS_YAML.format(exe=exe, missing=(tmp_path / "nowhere" / "app.exe").as_posix()), encoding="utf-8"
    )
    (data / "scenarios.yaml").write_text(SCENARIOS_YAML, encoding="utf-8")
    (data / "games.yaml").write_text(GAMES_YAML, encoding="utf-8")
    (data / "folders.yaml").write_text("folders:\n  allowed_roots: []\n", encoding="utf-8")
    return data


@pytest.fixture
def config(data_dir: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(
        bot_token="",
        authorized_user_ids=[111],
        authorized_chat_ids=[],
        data_dir=data_dir,
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
def service(config: AppConfig) -> ActionService:
    return ActionService(config)


@pytest.fixture
def dry_service(config: AppConfig) -> ActionService:
    return ActionService(config, dry_run=True)


class Recorder:
    """Stands in for a Windows side effect and remembers whether it was called."""

    def __init__(self, result: tuple[bool, str] = (True, "done")) -> None:
        self.calls: list[tuple] = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


@pytest.fixture
def forbid_processes(monkeypatch):
    """Any attempt to start a process fails the test."""
    import subprocess

    def boom(*args, **kwargs):
        raise AssertionError(f"unexpected process start: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)


URLS_YAML = """
urls:
  allowed_domains: ["www.youtube.com", "www.google.com", "open.spotify.com"]
  sites:
    youtube:
      name: "YouTube"
      url: "https://www.youtube.com/"
      search_url: "https://www.youtube.com/results?search_query={query}"
      aliases: ["youtube", "ютуб", "ютубе"]
    google:
      name: "Google"
      url: "https://www.google.com/"
      search_url: "https://www.google.com/search?q={query}"
      aliases: ["google", "гугл"]
    spotify_web:
      name: "Spotify web"
      url: "https://open.spotify.com/"
      aliases: ["спотифай веб"]
"""


@pytest.fixture(autouse=True)
def _urls_yaml(data_dir: Path):
    (data_dir / "urls.yaml").write_text(URLS_YAML, encoding="utf-8")
