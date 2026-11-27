from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.apps_service import open_allowed_app
from services.system_service import (
    get_health_status,
    get_heavy_processes,
    is_valid_host,
    mute_volume,
    ping_host,
    set_volume,
)
from utils.yaml_loader import load_yaml


@dataclass(slots=True)
class Scenario:
    key: str
    description: str
    require_confirmation: bool
    steps: list[dict[str, Any]]
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StepResult:
    ok: bool
    text: str


def load_scenarios(data_dir: Path) -> dict[str, Scenario]:
    data = load_yaml(data_dir / "scenarios.yaml")
    result: dict[str, Scenario] = {}

    for key, value in (data.get("scenarios") or {}).items():
        if not isinstance(value, dict):
            continue
        aliases = value.get("aliases") or []
        result[str(key)] = Scenario(
            key=str(key),
            description=str(value.get("description", "")),
            require_confirmation=bool(value.get("require_confirmation", False)),
            steps=[step for step in (value.get("steps") or []) if isinstance(step, dict)],
            aliases=[str(alias) for alias in aliases] if isinstance(aliases, list) else [],
        )

    return result


def run_scenario_steps(data_dir: Path, scenario_key: str, ping_default: str = "8.8.8.8") -> list[StepResult]:
    """Run a scenario's registered steps. Returns plain-text results, one per step."""
    scenario = load_scenarios(data_dir).get(scenario_key)

    if not scenario:
        return [StepResult(False, "Сценарий не найден.")]

    results: list[StepResult] = []

    for index, step in enumerate(scenario.steps, start=1):
        step_type = step.get("type")

        try:
            if step_type == "open_app":
                ok, message = open_allowed_app(data_dir, str(step.get("app", "")))
                results.append(StepResult(ok, f"{index}. {message}"))

            elif step_type == "health_check":
                health = get_health_status()
                results.append(
                    StepResult(
                        True,
                        f"{index}. CPU: {health.cpu_percent:.1f}% | "
                        f"RAM: {health.memory_percent:.1f}% | "
                        f"Disk: {health.disk_percent:.1f}%",
                    )
                )

            elif step_type == "ping_check":
                host = str(step.get("host", ping_default))
                ok, message = ping_host(host) if is_valid_host(host) else (False, "Ping error: invalid host")
                results.append(StepResult(ok, f"{index}. {message}"))

            elif step_type == "show_heavy_apps":
                heavy = get_heavy_processes(limit=5)
                if not heavy:
                    results.append(StepResult(True, f"{index}. Ничего тяжёлого не найдено."))
                else:
                    lines = [
                        f"   • {proc.name} (PID {proc.pid}) — CPU {proc.cpu_percent:.1f}% | RAM {proc.memory_mb:.1f} MB"
                        for proc in heavy
                    ]
                    results.append(StepResult(True, f"{index}. Топ приложений:\n" + "\n".join(lines)))

            elif step_type == "set_volume":
                level = step.get("level", 50)
                ok, message = set_volume(level) if isinstance(level, int) else (False, "Volume error: bad level")
                results.append(StepResult(ok, f"{index}. {message}"))

            elif step_type == "mute_volume":
                ok, message = mute_volume()
                results.append(StepResult(ok, f"{index}. {message}"))

            else:
                results.append(StepResult(False, f"{index}. Неизвестное действие."))

        except Exception as exc:  # one bad step must not abort the rest
            results.append(StepResult(False, f"{index}. Не удалось выполнить шаг: {exc}"))

    return results
