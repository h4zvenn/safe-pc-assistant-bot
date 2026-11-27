from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Callable
from typing import Any

from actions.models import (
    ActionError,
    ActionResult,
    ActionSpec,
    Actor,
    Channel,
    ParamSpec,
)
from config import AppConfig
from security.confirmations import ConfirmationStore
from services import (
    apps_service,
    desktop_service,
    files_service,
    gaming_service,
    media_service,
    scenarios_service,
    sites_service,
    system_service,
)

log = logging.getLogger("actions")


@dataclass(slots=True)
class Catalog:
    """What the user may refer to. Built from the validated YAML on every request."""

    apps: dict[str, tuple[str, list[str]]]  # id -> (display name, aliases)
    scenarios: dict[str, tuple[str, list[str]]]
    games: dict[str, tuple[str, list[str]]]
    sites: dict[str, tuple[str, list[str]]]
    folders: dict[str, tuple[str, list[str]]]


def _result(spec_or_id: ActionSpec | str, status: str, message: str, **kwargs: Any) -> ActionResult:
    action_id = spec_or_id.id if isinstance(spec_or_id, ActionSpec) else spec_or_id
    title = spec_or_id.title if isinstance(spec_or_id, ActionSpec) else ""
    return ActionResult(status=status, action_id=action_id, message=message, title=title, **kwargs)  # type: ignore[arg-type]


def _coerce_int(name: str, value: Any, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool):
        raise ActionError(f"{label}: нужно целое число.")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d{1,4}", value.strip()):
        value = int(value.strip())
    if not isinstance(value, int):
        raise ActionError(f"{label}: нужно целое число.")
    if not minimum <= value <= maximum:
        raise ActionError(f"{label} должна быть от {minimum} до {maximum}, получено {value}.", "out_of_range")
    return value


class ActionService:
    """The single entry point for Telegram, the local text mode and voice.

    Every request is (action_id, typed params). Ids and values are checked against the
    registry and the YAML catalog before anything touches Windows. Nothing here accepts
    a command line, a path, or a script from the caller.
    """

    def __init__(
        self,
        config: AppConfig,
        dry_run: bool = False,
        store: ConfirmationStore | None = None,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.store = store or ConfirmationStore()
        # Timers need somewhere to announce themselves; JARVIS registers this. Telegram has none.
        self.notifier: Callable[[str], None] | None = None
        self.timers: list[threading.Timer] = []
        self.specs: dict[str, ActionSpec] = {spec.id: spec for spec in self._build_specs()}

    # ---- catalog -------------------------------------------------------------------

    def catalog(self) -> Catalog:
        data_dir = self.config.data_dir
        return Catalog(
            apps={k: (a.display_name, a.aliases) for k, a in apps_service.load_apps(data_dir).items()},
            scenarios={
                k: (s.description or k, s.aliases) for k, s in scenarios_service.load_scenarios(data_dir).items()
            },
            games={k: (g.display_name, g.aliases) for k, g in gaming_service.load_games(data_dir).items()},
            sites={k: (s.name, s.aliases) for k, s in sites_service.load_sites(data_dir).items()},
            folders={k: (f.name, f.aliases) for k, f in desktop_service.load_folder_shortcuts(data_dir).items()},
        )

    # ---- validation ----------------------------------------------------------------

    def validate(self, action_id: str, params: dict[str, Any] | None) -> dict[str, Any]:
        spec = self.specs.get(action_id if isinstance(action_id, str) else "")
        if spec is None:
            raise ActionError("Неизвестное действие.", "unknown_action")
        params = dict(params or {})

        unexpected = set(params) - {p.name for p in spec.params}
        if unexpected:
            raise ActionError(f"Лишние параметры: {', '.join(sorted(map(str, unexpected)))}.")

        clean: dict[str, Any] = {}
        for param in spec.params:
            if params.get(param.name) in (None, ""):
                if param.required:
                    raise ActionError(f"Не указан параметр «{param.name}».", "missing_param")
                continue
            clean[param.name] = self._validate_param(param, params[param.name])
        return clean

    def _validate_param(self, param: ParamSpec, value: Any) -> Any:
        data_dir = self.config.data_dir
        if param.kind == "percent":
            return _coerce_int(param.name, value, 0, 100, "Громкость")
        if param.kind == "delta":
            delta = _coerce_int(param.name, value, -100, 100, "Изменение громкости")
            if delta == 0:
                raise ActionError("Изменение громкости не может быть нулевым.", "out_of_range")
            return delta
        if param.kind == "minutes":
            return _coerce_int(param.name, value, 1, 720, "Время таймера (минуты)")
        if param.kind == "limit":
            return _coerce_int(param.name, value, 1, 20, "Количество")
        if param.kind == "query":
            if not isinstance(value, str):
                raise ActionError("Поисковый запрос должен быть текстом.")
            query = sites_service.clean_query(value)
            if not query:
                raise ActionError("Пустой поисковый запрос.", "missing_param")
            if len(query) > sites_service.MAX_QUERY_CHARS:
                raise ActionError(f"Запрос длиннее {sites_service.MAX_QUERY_CHARS} символов.", "out_of_range")
            return query

        if not isinstance(value, str):
            raise ActionError(f"«{param.name}» должен быть строкой-идентификатором.")
        key = value.strip().lower()

        if param.kind in ("app_id", "closable_app_id"):
            app = apps_service.load_apps(data_dir).get(key)
            if app is None:
                raise ActionError(f"Неизвестное приложение «{key[:40]}».", "unknown_app")
            if param.kind == "closable_app_id" and not app.allow_close:
                raise ActionError(f"{app.display_name}: закрытие через ассистента не разрешено.", "close_denied")
            return key
        if param.kind == "scenario_id":
            if key not in scenarios_service.load_scenarios(data_dir):
                raise ActionError(f"Неизвестный сценарий «{key[:40]}».", "unknown_scenario")
            return key
        if param.kind == "game_id":
            if key not in gaming_service.load_games(data_dir):
                raise ActionError(f"Неизвестная игра «{key[:40]}».", "unknown_game")
            return key
        if param.kind == "folder_id":
            if key not in desktop_service.load_folder_shortcuts(data_dir):
                raise ActionError(f"Неизвестная папка «{key[:40]}».", "unknown_folder")
            return key
        if param.kind == "site_id":
            if key not in sites_service.load_sites(data_dir):
                raise ActionError(f"Неизвестный сайт «{key[:40]}».", "unknown_site")
            return key
        raise ActionError("Неподдерживаемый тип параметра.")

    # ---- execution -----------------------------------------------------------------

    def execute(self, action_id: str, params: dict[str, Any] | None, actor: Actor) -> ActionResult:
        try:
            clean = self.validate(action_id, params)
        except ActionError as exc:
            log.info("action rejected: id=%r channel=%s code=%s", str(action_id)[:40], actor.channel.value, exc.code)
            return _result(
                action_id if isinstance(action_id, str) else "?",
                "denied" if exc.code == "unknown_action" else "error",
                exc.message,
                code=exc.code,
            )

        spec = self.specs[action_id]

        if self._needs_confirmation(spec, clean):
            return self._request_confirmation(spec, clean, actor)

        return self._run(spec, clean, actor)

    def _needs_confirmation(self, spec: ActionSpec, params: dict[str, Any]) -> bool:
        if spec.sensitive:
            return True
        if spec.id == "run_scenario":
            scenario = scenarios_service.load_scenarios(self.config.data_dir).get(params["scenario_id"])
            return bool(scenario and scenario.require_confirmation)
        return False

    def _request_confirmation(self, spec: ActionSpec, params: dict[str, Any], actor: Actor) -> ActionResult:
        ttl = (
            self.config.confirmation_ttl_seconds
            if actor.group == "telegram"
            else self.config.local_confirmation_ttl_seconds
        )
        summary = self._describe(spec, params)
        pending = self.store.create(
            user_id=actor.user_id,
            group=actor.group,
            action_id=spec.id,
            params=params,
            summary=summary,
            ttl_seconds=ttl,
            created_by=actor.channel.value,
        )
        log.info("confirmation requested: id=%s channel=%s ttl=%ss", spec.id, actor.channel.value, ttl)
        return _result(
            spec,
            "needs_confirmation",
            f"Нужно подтверждение: {summary}. Действует {ttl} с.",
            code="needs_confirmation",
            confirmation=pending,
        )

    def confirm(self, token: str, actor: Actor) -> ActionResult:
        # A spoken "yes" is never a confirmation: only Telegram buttons and the local keyboard.
        if actor.channel is Channel.LOCAL_VOICE:
            return _result("confirm", "denied", "Голосом подтвердить нельзя: подтверди с клавиатуры.", code="voice_confirm")

        pending = self.store.take(str(token), actor.user_id, actor.group)
        if pending is None:
            return _result("confirm", "error", "Подтверждение не найдено или истекло.", code="confirmation_expired")

        spec = self.specs[pending.action_id]
        try:
            clean = self.validate(spec.id, pending.params)  # config may have changed meanwhile
        except ActionError as exc:
            return _result(spec, "error", exc.message, code=exc.code)
        log.info("confirmation accepted: id=%s channel=%s", spec.id, actor.channel.value)
        return self._run(spec, clean, actor)

    def cancel(self, token: str, actor: Actor) -> ActionResult:
        if self.store.cancel(str(token), actor.user_id, actor.group):
            return _result("cancel", "ok", "Действие отменено.", code="cancelled")
        return _result("cancel", "error", "Нечего отменять.", code="nothing_to_cancel")

    def _run(self, spec: ActionSpec, params: dict[str, Any], actor: Actor) -> ActionResult:
        if self.dry_run and spec.mutating:
            result = _result(spec, "ok", f"[dry-run] {self._describe(spec, params)}", code="dry_run")
        else:
            try:
                result = spec.handler(self, params)
            except Exception as exc:  # a failing action must not kill the bot or the voice loop
                log.exception("action failed: %s", spec.id)
                result = _result(spec, "error", f"Ошибка выполнения: {exc}", code="exception")
        log.info("action done: id=%s params=%s channel=%s status=%s", spec.id, params, actor.channel.value, result.status)
        return result

    def _describe(self, spec: ActionSpec, params: dict[str, Any]) -> str:
        catalog = self.catalog()
        name = params.get("app_id") or params.get("scenario_id") or params.get("game_id") or params.get("site_id") or params.get("folder_id")
        for group in (catalog.apps, catalog.scenarios, catalog.games, catalog.sites, catalog.folders):
            if name in group:
                name = group[name][0]
                break
        suffix = f" ({name})" if name else ""
        if "query" in params:
            suffix = f" ({name}: «{params['query']}»)"
        if "delta" in params:
            suffix = f" ({params['delta']:+d})"
        if "minutes" in params:
            suffix = f" ({params['minutes']} мин)"
        if "percent" in params:
            suffix = f" ({params['percent']}%)"
        return f"{spec.description}{suffix}"

    # ---- timers --------------------------------------------------------------------

    def _timer_fired(self, minutes: int) -> None:
        log.info("timer fired: %s min", minutes)
        if self.notifier is not None:
            try:
                self.notifier(f"Таймер на {minutes} мин сработал.")
            except Exception:  # a broken notifier must not kill the timer thread silently
                log.exception("timer notifier failed")

    # ---- handlers ------------------------------------------------------------------

    def _build_specs(self) -> list[ActionSpec]:
        app = ParamSpec("app_id", "app_id")
        closable = ParamSpec("app_id", "closable_app_id")
        scenario = ParamSpec("scenario_id", "scenario_id")
        game = ParamSpec("game_id", "game_id")
        site = ParamSpec("site_id", "site_id")
        folder = ParamSpec("folder_id", "folder_id")
        delta = ParamSpec("delta", "delta")
        minutes = ParamSpec("minutes", "minutes")
        query = ParamSpec("query", "query")
        percent = ParamSpec("percent", "percent")
        limit = ParamSpec("limit", "limit", required=False)
        return [
            ActionSpec("open_app", "📦 Приложения", "Открыть приложение", _open_app, (app,)),
            ActionSpec("close_app", "📦 Приложения", "Закрыть приложение", _close_app, (closable,), sensitive=True),
            ActionSpec("list_apps", "📦 Приложения", "Список приложений", _list_apps, mutating=False),
            ActionSpec("running_apps", "🖥 Запущенные приложения", "Запущенные приложения", _running_apps, mutating=False),
            ActionSpec("system_status", "🖥 Система", "Состояние ПК", _system_status, mutating=False),
            ActionSpec("top_processes", "📊 Процессы", "Топ процессов", _top_processes, (limit,), mutating=False),
            ActionSpec("ping", "🌐 Ping", "Проверка сети", _ping, mutating=False),
            ActionSpec("gaming_status", "🎮 Игры", "Игровой статус", _gaming_status, mutating=False),
            ActionSpec("launch_game", "🎮 Игры", "Запустить игру", _launch_game, (game,)),
            ActionSpec("open_site", "🌐 Сайты", "Открыть сайт", _open_site, (site,)),
            ActionSpec("search_site", "🌐 Сайты", "Найти на сайте", _search_site, (site, query)),
            ActionSpec("list_sites", "🌐 Сайты", "Список сайтов", _list_sites, mutating=False),
            ActionSpec("volume_change", "🎵 Медиа", "Изменить громкость (громче или тише)", _volume_change, (delta,)),
            ActionSpec("lock_pc", "🖥 Система", "Заблокировать экран", _lock_pc),
            ActionSpec("show_desktop", "🖥 Система", "Свернуть все окна", _show_desktop),
            ActionSpec("screenshot", "🖥 Система", "Сделать скриншот", _screenshot),
            ActionSpec("current_time", "🕒 Время", "Сказать время и дату", _current_time, mutating=False),
            ActionSpec("open_folder", "📁 Папки", "Открыть папку", _open_folder, (folder,)),
            ActionSpec("set_timer", "⏲ Таймер", "Поставить таймер на N минут", _set_timer, (minutes,)),
            ActionSpec("cancel_timers", "⏲ Таймер", "Отменить таймеры", _cancel_timers),
            ActionSpec("list_files", "📁 Файлы", "Список разрешённых файлов", _list_files, mutating=False),
            ActionSpec("set_volume", "🎵 Медиа", "Установить громкость", _set_volume, (percent,)),
            ActionSpec("mute", "🎵 Медиа", "Выключить звук", _mute),
            ActionSpec("unmute", "🎵 Медиа", "Включить звук", _unmute),
            ActionSpec("media_pause", "🎵 Медиа", "Поставить медиа на паузу", _media("pause")),
            ActionSpec("media_play", "🎵 Медиа", "Продолжить воспроизведение", _media("play")),
            ActionSpec("media_next", "🎵 Медиа", "Следующий трек", _media("next")),
            ActionSpec("media_previous", "🎵 Медиа", "Предыдущий трек", _media("previous")),
            ActionSpec("list_scenarios", "🚀 Сценарии", "Список сценариев", _list_scenarios, mutating=False),
            ActionSpec("run_scenario", "🚀 Сценарии", "Запустить сценарий", _run_scenario, (scenario,)),
            ActionSpec("power_shutdown", "⚡ Питание", "Выключить ПК", _power("shutdown"), sensitive=True),
            ActionSpec("power_restart", "⚡ Питание", "Перезагрузить ПК", _power("restart"), sensitive=True),
            ActionSpec("power_cancel", "⚡ Питание", "Отменить выключение или перезагрузку", _power("cancel")),
        ]


def _open_app(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = apps_service.open_allowed_app(svc.config.data_dir, p["app_id"])
    return _result(svc.specs["open_app"], "ok" if ok else "error", message, code="app_opened" if ok else "app_failed", data=p)


def _close_app(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = apps_service.close_allowed_app(svc.config.data_dir, p["app_id"])
    return _result(svc.specs["close_app"], "ok" if ok else "error", message, code="app_closed" if ok else "app_close_failed", data=p)


def _list_apps(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    apps = apps_service.load_apps(svc.config.data_dir)
    if not apps:
        return _result(svc.specs["list_apps"], "ok", "Список пуст.")
    lines = [f"• {key} — {app.display_name}" for key, app in apps.items()]
    return _result(svc.specs["list_apps"], "ok", "\n".join(lines), data={"apps": list(apps)})


def _running_apps(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    items = apps_service.running_allowlisted_apps(svc.config.data_dir)
    message = "\n".join(f"• {item}" for item in items) if items else "Ничего не найдено."
    return _result(svc.specs["running_apps"], "ok", message, data={"apps": items})


def _system_status(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    snap = system_service.get_system_snapshot()
    message = (
        f"CPU: {snap['cpu_percent']:.1f}%\n"
        f"RAM: {snap['memory_percent']:.1f}% ({snap['memory_used_gb']:.1f} из {snap['memory_total_gb']:.1f} ГБ)\n"
        f"Диск: {snap['disk_percent']:.1f}%\n"
        f"Аптайм: {snap['uptime_hours']:.1f} ч"
    )
    return _result(svc.specs["system_status"], "ok", message, code="system_status", data=snap)


def _top_processes(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    rows = system_service.top_processes(limit=p.get("limit", 5))
    if not rows:
        return _result(svc.specs["top_processes"], "ok", "Список пуст.")
    lines = [
        f"• {row['name']} (PID {row['pid']}) — CPU {row['cpu_percent']:.1f}% | RAM {row['memory_mb']:.1f} MB"
        for row in rows
    ]
    return _result(svc.specs["top_processes"], "ok", "\n".join(lines), data={"processes": rows})


def _ping(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = system_service.ping_host(svc.config.ping_host)
    return _result(svc.specs["ping"], "ok" if ok else "error", message, code="ping_ok" if ok else "ping_failed")


def _gaming_status(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    snap = gaming_service.get_gaming_snapshot(svc.config.data_dir)
    system = system_service.get_system_snapshot()
    detected = snap.detected_game or "не обнаружена"
    active = ", ".join(snap.active_items) if snap.active_items else "ничего не найдено"
    message = (
        f"Текущая игра: {detected}\nАктивно: {active}\n\n"
        f"CPU: {system['cpu_percent']:.1f}% | RAM: {system['memory_percent']:.1f}% | Диск: {system['disk_percent']:.1f}%"
    )
    return _result(svc.specs["gaming_status"], "ok", message, data={"game": snap.detected_game})


def _launch_game(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = gaming_service.launch_game(svc.config.data_dir, p["game_id"])
    return _result(svc.specs["launch_game"], "ok" if ok else "error", message, code="game_launched" if ok else "game_failed", data=p)


def _open_site(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = sites_service.open_site(svc.config.data_dir, p["site_id"])
    return _result(svc.specs["open_site"], "ok" if ok else "error", message, code="site_opened" if ok else "site_failed", data=p)


def _search_site(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = sites_service.search_site(svc.config.data_dir, p["site_id"], p["query"])
    return _result(svc.specs["search_site"], "ok" if ok else "error", message, code="site_searched" if ok else "site_failed", data=p)


def _volume_change(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message, target = system_service.change_volume(p["delta"])
    return _result(
        svc.specs["volume_change"], "ok" if ok else "error", message,
        code="volume_set" if ok else "volume_failed", data={"percent": target, **p},
    )


def _lock_pc(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = desktop_service.lock_pc()
    return _result(svc.specs["lock_pc"], "ok" if ok else "error", message, code="locked" if ok else "lock_failed")


def _show_desktop(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = desktop_service.show_desktop()
    return _result(svc.specs["show_desktop"], "ok" if ok else "error", message, code="desktop_shown")


def _screenshot(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = desktop_service.take_screenshot()
    return _result(svc.specs["screenshot"], "ok" if ok else "error", message, code="screenshot_taken")


def _current_time(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    message, data = desktop_service.current_time()
    return _result(svc.specs["current_time"], "ok", message, code="current_time", data=data)


def _open_folder(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = desktop_service.open_folder(svc.config.data_dir, p["folder_id"])
    return _result(svc.specs["open_folder"], "ok" if ok else "error", message, code="folder_opened" if ok else "folder_failed", data=p)


def _set_timer(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    if svc.notifier is None:
        return _result(svc.specs["set_timer"], "error", "Таймеры работают только в режиме JARVIS (там есть куда сообщить).", code="timer_unavailable")
    minutes = p["minutes"]
    svc.timers = [t for t in svc.timers if t.is_alive()]
    timer = threading.Timer(minutes * 60, svc._timer_fired, args=(minutes,))
    timer.daemon = True
    timer.start()
    svc.timers.append(timer)
    return _result(svc.specs["set_timer"], "ok", f"Таймер на {minutes} мин запущен.", code="timer_set", data=p)


def _cancel_timers(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    active = [t for t in svc.timers if t.is_alive()]
    for timer in active:
        timer.cancel()
    svc.timers = []
    if not active:
        return _result(svc.specs["cancel_timers"], "ok", "Активных таймеров нет.", code="timer_none")
    return _result(svc.specs["cancel_timers"], "ok", f"Таймеров отменено: {len(active)}.", code="timer_cancelled")


def _list_sites(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    sites = sites_service.load_sites(svc.config.data_dir)
    message = "\n".join(f"• {key} — {site.name}" for key, site in sites.items()) or "Список пуст."
    return _result(svc.specs["list_sites"], "ok", message, data={"sites": list(sites)})


def _list_files(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    items = files_service.list_allowed_files(svc.config.data_dir)
    if not items:
        return _result(svc.specs["list_files"], "ok", "Список пуст.")
    shown = items[:20]
    message = "\n".join(f"• {item}" for item in shown)
    if len(items) > len(shown):
        message += f"\n\nПоказано: {len(shown)} из {len(items)}"
    return _result(svc.specs["list_files"], "ok", message, data={"files": items})


def _set_volume(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = system_service.set_volume(p["percent"])
    return _result(svc.specs["set_volume"], "ok" if ok else "error", message, code="volume_set" if ok else "volume_failed", data=p)


def _mute(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = system_service.mute_volume()
    return _result(svc.specs["mute"], "ok" if ok else "error", message, code="muted" if ok else "volume_failed")


def _unmute(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    ok, message = system_service.unmute_volume()
    return _result(svc.specs["unmute"], "ok" if ok else "error", message, code="unmuted" if ok else "volume_failed")


def _media(command: str):
    action_id = f"media_{command}"

    def handler(svc: ActionService, p: dict[str, Any]) -> ActionResult:
        ok, message = media_service.media_control(command)  # type: ignore[arg-type]
        return _result(svc.specs[action_id], "ok" if ok else "error", message, code=action_id if ok else "media_failed")

    return handler


def _list_scenarios(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    scenarios = scenarios_service.load_scenarios(svc.config.data_dir)
    if not scenarios:
        return _result(svc.specs["list_scenarios"], "ok", "Список пуст.")
    lines = [f"• {key} — {s.description}" for key, s in scenarios.items()]
    return _result(svc.specs["list_scenarios"], "ok", "\n".join(lines), data={"scenarios": list(scenarios)})


def _run_scenario(svc: ActionService, p: dict[str, Any]) -> ActionResult:
    steps = scenarios_service.run_scenario_steps(svc.config.data_dir, p["scenario_id"], svc.config.ping_host)
    scenario = scenarios_service.load_scenarios(svc.config.data_dir)[p["scenario_id"]]
    title = scenario.description.strip() or scenario.key
    oks = [s.ok for s in steps]
    status = "ok" if all(oks) else ("partial" if any(oks) else "error")
    message = f"{title}\n" + "\n".join(s.text for s in steps)
    failed = [s.text for s in steps if not s.ok]
    return _result(
        svc.specs["run_scenario"], status, message, code=f"scenario_{status}", data={**p, "failed": failed}
    )


def _power(kind: str):
    action_id = f"power_{kind}"

    def handler(svc: ActionService, p: dict[str, Any]) -> ActionResult:
        delay = svc.config.shutdown_delay_seconds
        if kind == "shutdown":
            ok, message = system_service.shutdown_pc(delay)
        elif kind == "restart":
            ok, message = system_service.restart_pc(delay)
        else:
            ok, message = system_service.cancel_shutdown()
        return _result(svc.specs[action_id], "ok" if ok else "error", message, code=action_id if ok else "power_failed")

    return handler
