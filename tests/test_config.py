from __future__ import annotations

import os
from pathlib import Path

import pytest

import config as config_module
from config import AppConfig
from services.config_validation import require_valid_config, validate_data_dir
from utils.paths import PROJECT_ROOT, expand_path
from utils.yaml_loader import ConfigError, load_yaml

REAL_DATA = PROJECT_ROOT / "data"

ENV_KEYS = [
    "BOT_TOKEN", "AUTHORIZED_USER_IDS", "AUTHORIZED_CHAT_IDS", "DEFAULT_PARSE_MODE", "DATA_DIR", "LOG_DIR",
    "CONFIRMATION_TTL_SECONDS", "RATE_LIMIT_COMMANDS_PER_MINUTE", "ALERTS_ENABLED",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))


# ---- AppConfig ---------------------------------------------------------------------


def test_local_mode_needs_no_token():
    cfg = AppConfig.from_env(require_telegram=False)
    assert cfg.bot_token == ""


def test_telegram_mode_requires_token_and_authorization(monkeypatch):
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        AppConfig.from_env()
    monkeypatch.setenv("BOT_TOKEN", "123456:replace-with-token-from-botfather")
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):  # the .env.example placeholder is not a token
        AppConfig.from_env()
    monkeypatch.setenv("BOT_TOKEN", "123456789:AAH-real-looking-token-value-0123456789")
    with pytest.raises(RuntimeError, match="AUTHORIZED_USER_IDS"):
        AppConfig.from_env()
    monkeypatch.setenv("AUTHORIZED_USER_IDS", "42, 43")
    assert AppConfig.from_env().authorized_user_ids == [42, 43]


def test_bad_values_give_readable_errors(monkeypatch):
    monkeypatch.setenv("AUTHORIZED_USER_IDS", "42,abc")
    with pytest.raises(RuntimeError, match="AUTHORIZED_USER_IDS"):
        AppConfig.from_env(require_telegram=False)
    monkeypatch.setenv("AUTHORIZED_USER_IDS", "42")
    monkeypatch.setenv("CONFIRMATION_TTL_SECONDS", "soon")
    with pytest.raises(RuntimeError, match="CONFIRMATION_TTL_SECONDS"):
        AppConfig.from_env(require_telegram=False)


def test_markdown_parse_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("DEFAULT_PARSE_MODE", "MARKDOWN")
    with pytest.raises(RuntimeError, match="HTML"):
        AppConfig.from_env(require_telegram=False)


def test_relative_data_dir_is_resolved_from_project_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", "data")
    assert AppConfig.from_env(require_telegram=False).data_dir == (PROJECT_ROOT / "data").resolve()


def test_env_example_covers_every_variable_the_code_reads():
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    declared = {line.split("=")[0].strip().lstrip("#").strip() for line in text.splitlines() if "=" in line}
    source = "".join(
        (PROJECT_ROOT / name).read_text(encoding="utf-8") for name in ("config.py", "jarvis/settings.py")
    )
    import re

    used = set(re.findall(r'(?:getenv|_parse_int_env|_env_path)\(\s*"([A-Z_]+)"', source))
    assert used - declared == set(), f"missing from .env.example: {sorted(used - declared)}"


def test_env_example_has_no_real_secrets():
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    import re

    assert not re.search(r"\d{8,}:[A-Za-z0-9_-]{30,}", text)


def test_dotenv_is_git_ignored():
    ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored


# ---- Windows paths -----------------------------------------------------------------


def test_expand_path_uses_environment(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"D:\Profiles\me\Local")
    assert expand_path("%LOCALAPPDATA%/Discord/Update.exe") == Path(r"D:\Profiles\me\Local\Discord\Update.exe")


def test_expand_path_falls_back_when_variables_are_missing(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\someone")
    assert expand_path("%LOCALAPPDATA%/x") == Path(r"C:\Users\someone\AppData\Local\x")
    assert expand_path("%APPDATA%/x") == Path(r"C:\Users\someone\AppData\Roaming\x")


def test_unknown_variable_is_left_alone_and_never_resolves_to_a_real_file():
    assert "%NO_SUCH_VAR_123%" in str(expand_path("%NO_SUCH_VAR_123%/x.exe"))


def test_no_hardcoded_user_directory_remains():
    # scripts.yaml, urls.yaml and users.yaml are reserved: no code reads them yet.
    for name in ("apps", "folders", "games", "scenarios"):
        text = (REAL_DATA / f"{name}.yaml").read_text(encoding="utf-8").lower()
        assert "offku" not in text and "c:/users/" not in text, name


# ---- YAML validation ---------------------------------------------------------------


def test_bundled_data_is_valid():
    errors = [i for i in validate_data_dir(REAL_DATA) if i.level == "error"]
    assert errors == []


def test_every_bundled_scenario_step_refers_to_a_known_app():
    require_valid_config(REAL_DATA)
    scenarios = load_yaml(REAL_DATA / "scenarios.yaml")["scenarios"]
    assert "gaming" in scenarios  # the voice command "игровой режим" depends on it


def write(data_dir: Path, name: str, text: str) -> None:
    (data_dir / name).write_text(text, encoding="utf-8")


def test_validation_reports_every_problem(data_dir):
    write(data_dir, "scenarios.yaml", """
scenarios:
  bad:
    steps:
      - type: open_app
        app: ghost
      - type: set_volume
        level: 150
      - type: ping_check
        host: "-t"
      - type: run_shell
        command: calc
""")
    messages = [str(i) for i in validate_data_dir(data_dir) if i.level == "error"]
    assert any("ghost" in m for m in messages)
    assert any("0..100" in m for m in messages)
    assert any("invalid host" in m for m in messages)
    assert any("unknown step type" in m for m in messages)
    with pytest.raises(ConfigError):
        require_valid_config(data_dir)


def test_broken_yaml_is_reported_not_crashed(data_dir):
    write(data_dir, "apps.yaml", "apps: [unclosed")
    with pytest.raises(ConfigError, match="apps.yaml"):
        require_valid_config(data_dir)


def test_non_mapping_yaml_is_rejected(data_dir):
    write(data_dir, "games.yaml", "- just\n- a list\n")
    with pytest.raises(ConfigError):
        require_valid_config(data_dir)


def test_missing_executables_are_warnings_not_errors(data_dir):
    issues = validate_data_dir(data_dir)
    assert any(i.level == "warning" and "spotify" in i.message for i in issues)
    assert not [i for i in issues if i.level == "error"]


def test_app_path_must_be_declared(data_dir):
    write(data_dir, "apps.yaml", "apps:\n  steam:\n    name: Steam\n")
    with pytest.raises(ConfigError, match="paths"):
        require_valid_config(data_dir)


def test_bad_app_id_is_rejected(data_dir):
    write(data_dir, "apps.yaml", "apps:\n  'ste am;calc':\n    paths: ['C:/x.exe']\n")
    with pytest.raises(ConfigError, match="id must match"):
        require_valid_config(data_dir)


def test_files_listing_expands_windows_variables_and_filters(data_dir, tmp_path, monkeypatch):
    from services.files_service import list_allowed_files

    shared = tmp_path / "Shared"
    shared.mkdir()
    (shared / "note.txt").write_text("x", encoding="utf-8")
    (shared / "tool.exe").write_text("x", encoding="utf-8")
    monkeypatch.setenv("JARVIS_TEST_ROOT", str(tmp_path))
    write(
        data_dir,
        "folders.yaml",
        'folders:\n  allowed_roots: ["%JARVIS_TEST_ROOT%/Shared"]\nlimits:\n  allowed_extensions: [".txt"]\n',
    )
    assert [Path(p).name for p in list_allowed_files(data_dir)] == ["note.txt"]
