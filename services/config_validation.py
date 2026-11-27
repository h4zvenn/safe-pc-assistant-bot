from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.apps_service import load_apps, resolve_app_path
from services.sites_service import url_is_allowed
from utils.paths import expand_path
from utils.yaml_loader import ConfigError, load_yaml

ID_PATTERN = re.compile(r"^[a-z0-9_]{1,40}$")
HOST_PATTERN = re.compile(r"^(?!-)[A-Za-z0-9.\-]{1,253}$")
STEP_TYPES = {
    "open_app",
    "health_check",
    "ping_check",
    "show_heavy_apps",
    "set_volume",
    "mute_volume",
}


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    level: str  # "error" blocks startup, "warning" is informational
    file: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.file}: {self.message}"


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_apps(data_dir: Path, issues: list[ConfigIssue]) -> None:
    raw = load_yaml(data_dir / "apps.yaml")
    apps = raw.get("apps")
    if not isinstance(apps, dict) or not apps:
        issues.append(ConfigIssue("error", "apps.yaml", "'apps' must be a non-empty mapping"))
        return

    for key, value in apps.items():
        where = f"apps.{key}"
        if not isinstance(key, str) or not ID_PATTERN.match(key):
            issues.append(ConfigIssue("error", "apps.yaml", f"{where}: id must match [a-z0-9_]"))
        if not isinstance(value, dict):
            issues.append(ConfigIssue("error", "apps.yaml", f"{where}: must be a mapping"))
            continue
        paths = value.get("paths", value.get("path"))
        if isinstance(paths, str):
            paths = [paths]
        if not _is_str_list(paths) or not paths:
            issues.append(ConfigIssue("error", "apps.yaml", f"{where}: 'paths' must list at least one executable"))
        for field_name in ("args", "process_names", "aliases"):
            if field_name in value and value[field_name] is not None and not _is_str_list(value[field_name]):
                issues.append(ConfigIssue("error", "apps.yaml", f"{where}.{field_name}: must be a list of strings"))
        if "allow_close" in value and not isinstance(value["allow_close"], bool):
            issues.append(ConfigIssue("error", "apps.yaml", f"{where}.allow_close: must be true or false"))

    for app in load_apps(data_dir).values():
        if resolve_app_path(app) is None:
            issues.append(
                ConfigIssue("warning", "apps.yaml", f"{app.key}: executable not found (reported only if used)")
            )


def _validate_scenarios(data_dir: Path, issues: list[ConfigIssue]) -> None:
    raw = load_yaml(data_dir / "scenarios.yaml")
    scenarios = raw.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        issues.append(ConfigIssue("error", "scenarios.yaml", "'scenarios' must be a non-empty mapping"))
        return

    app_ids = set(load_apps(data_dir))

    for key, value in scenarios.items():
        where = f"scenarios.{key}"
        if not isinstance(key, str) or not ID_PATTERN.match(key):
            issues.append(ConfigIssue("error", "scenarios.yaml", f"{where}: id must match [a-z0-9_]"))
        if not isinstance(value, dict):
            issues.append(ConfigIssue("error", "scenarios.yaml", f"{where}: must be a mapping"))
            continue
        if "require_confirmation" in value and not isinstance(value["require_confirmation"], bool):
            issues.append(ConfigIssue("error", "scenarios.yaml", f"{where}.require_confirmation: must be true or false"))
        if "aliases" in value and value["aliases"] is not None and not _is_str_list(value["aliases"]):
            issues.append(ConfigIssue("error", "scenarios.yaml", f"{where}.aliases: must be a list of strings"))
        steps = value.get("steps")
        if not isinstance(steps, list) or not steps:
            issues.append(ConfigIssue("error", "scenarios.yaml", f"{where}.steps: must be a non-empty list"))
            continue
        for index, step in enumerate(steps, start=1):
            at = f"{where}.steps[{index}]"
            if not isinstance(step, dict) or step.get("type") not in STEP_TYPES:
                issues.append(
                    ConfigIssue("error", "scenarios.yaml", f"{at}: unknown step type (allowed: {sorted(STEP_TYPES)})")
                )
                continue
            step_type = step["type"]
            if step_type == "open_app" and step.get("app") not in app_ids:
                issues.append(ConfigIssue("error", "scenarios.yaml", f"{at}: app {step.get('app')!r} is not in apps.yaml"))
            if step_type == "set_volume":
                level = step.get("level")
                if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
                    issues.append(ConfigIssue("error", "scenarios.yaml", f"{at}: level must be an integer 0..100"))
            if step_type == "ping_check" and "host" in step:
                if not isinstance(step["host"], str) or not HOST_PATTERN.match(step["host"]):
                    issues.append(ConfigIssue("error", "scenarios.yaml", f"{at}: invalid host"))


def _validate_games(data_dir: Path, issues: list[ConfigIssue]) -> None:
    raw = load_yaml(data_dir / "games.yaml")
    for section in ("games", "launchers"):
        block = raw.get(section) or {}
        if not isinstance(block, dict):
            issues.append(ConfigIssue("error", "games.yaml", f"'{section}' must be a mapping"))
            continue
        for key, value in block.items():
            if not isinstance(value, dict):
                issues.append(ConfigIssue("error", "games.yaml", f"{section}.{key}: must be a mapping"))
                continue
            if "process_names" in value and not _is_str_list(value["process_names"]):
                issues.append(
                    ConfigIssue("error", "games.yaml", f"{section}.{key}.process_names: must be a list of strings")
                )
            steam_id = value.get("steam_app_id")
            if steam_id is not None and (isinstance(steam_id, bool) or not isinstance(steam_id, int) or steam_id <= 0):
                issues.append(
                    ConfigIssue("error", "games.yaml", f"{section}.{key}.steam_app_id: must be a positive integer")
                )


def _validate_folders(data_dir: Path, issues: list[ConfigIssue]) -> None:
    raw = load_yaml(data_dir / "folders.yaml")
    roots = (raw.get("folders") or {}).get("allowed_roots") or []
    if not _is_str_list(roots):
        issues.append(ConfigIssue("error", "folders.yaml", "folders.allowed_roots must be a list of strings"))
        return
    for root in roots:
        if not expand_path(root).is_dir():
            issues.append(ConfigIssue("warning", "folders.yaml", f"folder not found: {root}"))

    shortcuts = raw.get("shortcuts") or {}
    if not isinstance(shortcuts, dict):
        issues.append(ConfigIssue("error", "folders.yaml", "'shortcuts' must be a mapping"))
        return
    for key, value in shortcuts.items():
        where = f"shortcuts.{key}"
        if not isinstance(key, str) or not ID_PATTERN.match(key):
            issues.append(ConfigIssue("error", "folders.yaml", f"{where}: id must match [a-z0-9_]"))
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            issues.append(ConfigIssue("error", "folders.yaml", f"{where}: needs a 'path' string"))
            continue
        if value.get("aliases") is not None and not _is_str_list(value["aliases"]):
            issues.append(ConfigIssue("error", "folders.yaml", f"{where}.aliases: must be a list of strings"))
        if not expand_path(value["path"]).is_dir():
            issues.append(ConfigIssue("warning", "folders.yaml", f"{where}: folder not found: {value['path']}"))


def _validate_urls(data_dir: Path, issues: list[ConfigIssue]) -> None:
    raw = load_yaml(data_dir / "urls.yaml").get("urls") or {}
    if not isinstance(raw, dict):
        issues.append(ConfigIssue("error", "urls.yaml", "'urls' must be a mapping"))
        return
    domains = raw.get("allowed_domains") or []
    if not _is_str_list(domains):
        issues.append(ConfigIssue("error", "urls.yaml", "allowed_domains must be a list of strings"))
        return
    hosts = {d.lower() for d in domains}
    sites = raw.get("sites") or {}
    if not isinstance(sites, dict):
        issues.append(ConfigIssue("error", "urls.yaml", "'sites' must be a mapping"))
        return
    for key, value in sites.items():
        where = f"sites.{key}"
        if not isinstance(key, str) or not ID_PATTERN.match(key):
            issues.append(ConfigIssue("error", "urls.yaml", f"{where}: id must match [a-z0-9_]"))
        if not isinstance(value, dict):
            issues.append(ConfigIssue("error", "urls.yaml", f"{where}: must be a mapping"))
            continue
        if not isinstance(value.get("url"), str) or not url_is_allowed(value["url"], hosts):
            issues.append(ConfigIssue("error", "urls.yaml", f"{where}.url: must be https on a host from allowed_domains"))
        search = value.get("search_url")
        if search is not None:
            if not isinstance(search, str) or "{query}" not in search or not url_is_allowed(search.replace("{query}", "x"), hosts):
                issues.append(
                    ConfigIssue("error", "urls.yaml", f"{where}.search_url: https on an allowed host and must contain {{query}}")
                )
        if "aliases" in value and value["aliases"] is not None and not _is_str_list(value["aliases"]):
            issues.append(ConfigIssue("error", "urls.yaml", f"{where}.aliases: must be a list of strings"))


def validate_data_dir(data_dir: Path) -> list[ConfigIssue]:
    """Validate every data/*.yaml the assistant reads. Never touches the system."""
    issues: list[ConfigIssue] = []
    for check in (_validate_apps, _validate_scenarios, _validate_games, _validate_folders, _validate_urls):
        try:
            check(data_dir, issues)
        except ConfigError as exc:
            issues.append(ConfigIssue("error", str(exc).split(":", 1)[0], str(exc)))
    return issues


def require_valid_config(data_dir: Path) -> list[ConfigIssue]:
    """Return warnings; raise ConfigError listing every error."""
    issues = validate_data_dir(data_dir)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise ConfigError("Invalid configuration:\n" + "\n".join(f"  {issue}" for issue in errors))
    return [issue for issue in issues if issue.level == "warning"]
