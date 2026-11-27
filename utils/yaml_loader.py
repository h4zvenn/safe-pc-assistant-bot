from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """A data/*.yaml file is unreadable or has the wrong shape."""


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path.name}: cannot read YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping, got {type(data).__name__}")
    return data
