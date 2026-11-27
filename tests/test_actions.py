from __future__ import annotations

from pathlib import Path

import pytest

from actions import ActionService, Actor, Channel
from services import apps_service, gaming_service, media_service, system_service
from tests.conftest import Recorder

LOCAL = Actor(Channel.LOCAL_TEXT)
VOICE = Actor(Channel.LOCAL_VOICE)
TG = Actor(Channel.TELEGRAM, 111)


# ---- parameter validation ----------------------------------------------------------


def test_unknown_app_id_is_rejected(service, forbid_processes):
    result = service.execute("open_app", {"app_id": "malware"}, LOCAL)
    assert result.status == "error"
    assert result.code == "unknown_app"


@pytest.mark.parametrize(
    "app_id",
    ["steam; calc", "../../Windows/System32/cmd.exe", "C:/Windows/notepad.exe", "steam && whoami", "", None, 5, ["steam"]],
)
def test_app_id_must_be_a_registered_id_not_a_path_or_command(service, forbid_processes, app_id):
    result = service.execute("open_app", {"app_id": app_id}, LOCAL)
    assert result.status == "error"


@pytest.mark.parametrize("percent", [101, 150, -1, 1000, "abc", "30%", 30.5, True, None, "", [30]])
def test_volume_above_100_or_garbage_is_rejected(service, monkeypatch, percent):
    setter = Recorder()
    monkeypatch.setattr(system_service, "set_volume", setter)
    result = service.execute("set_volume", {"percent": percent}, LOCAL)
    assert result.status == "error"
    assert setter.calls == []


@pytest.mark.parametrize("percent,expected", [(0, 0), (30, 30), (100, 100), ("45", 45), (30.0, 30)])
def test_valid_volume_reaches_windows_layer(service, monkeypatch, percent, expected):
    setter = Recorder((True, "ok"))
    monkeypatch.setattr(system_service, "set_volume", setter)
    result = service.execute("set_volume", {"percent": percent}, LOCAL)
    assert result.status == "ok"
    assert setter.calls == [((expected,), {})]


def test_unregistered_action_is_denied(service, forbid_processes):
    for action_id in ("powershell", "run_command", "cmd", "open_url", "", None, ["open_app"]):
        result = service.execute(action_id, {"command": "calc.exe"}, LOCAL)
        assert result.status == "denied"


def test_extra_parameters_are_rejected(service, forbid_processes):
    result = service.execute("open_app", {"app_id": "steam", "args": ["--evil"], "path": "C:/x.exe"}, LOCAL)
    assert result.status == "error"


def test_missing_parameter_is_reported(service):
    result = service.execute("open_app", {}, LOCAL)
    assert result.status == "error"
    assert result.code == "missing_param"


# ---- opening apps ------------------------------------------------------------------


def test_open_app_launches_configured_executable_without_shell(service, monkeypatch):
    started = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            started.append((argv, kwargs))

    monkeypatch.setattr(apps_service.subprocess, "Popen", FakePopen)
    result = service.execute("open_app", {"app_id": "discord"}, LOCAL)
    assert result.status == "ok"
    argv, kwargs = started[0]
    assert isinstance(argv, list) and argv[1:] == ["--processStart", "Discord.exe"]
    assert kwargs["shell"] is False


def test_missing_app_gives_clear_error_only_for_that_app(service, forbid_processes):
    result = service.execute("open_app", {"app_id": "spotify"}, LOCAL)
    assert result.status == "error"
    assert "Spotify" in result.message and "не найден" in result.message


def test_other_apps_still_work_when_one_is_missing(service, monkeypatch):
    monkeypatch.setattr(apps_service.subprocess, "Popen", lambda *a, **k: None)
    assert service.execute("open_app", {"app_id": "steam"}, LOCAL).status == "ok"


# ---- confirmations -----------------------------------------------------------------


@pytest.mark.parametrize("action_id,params", [("power_shutdown", {}), ("power_restart", {}), ("close_app", {"app_id": "steam"})])
def test_sensitive_actions_need_confirmation_and_do_nothing_before_it(service, monkeypatch, forbid_processes, action_id, params):
    spies = {name: Recorder() for name in ("shutdown_pc", "restart_pc", "cancel_shutdown")}
    for name, spy in spies.items():
        monkeypatch.setattr(system_service, name, spy)
    monkeypatch.setattr(apps_service, "close_allowed_app", Recorder())

    result = service.execute(action_id, params, LOCAL)

    assert result.status == "needs_confirmation"
    assert result.confirmation is not None
    assert all(not spy.calls for spy in spies.values())


def test_shutdown_runs_only_after_local_confirmation(service, monkeypatch):
    shutdown = Recorder((True, "scheduled"))
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    pending = service.execute("power_shutdown", {}, LOCAL).confirmation

    assert service.confirm(pending.token, LOCAL).status == "ok"
    assert len(shutdown.calls) == 1
    # one-shot
    assert service.confirm(pending.token, LOCAL).status == "error"
    assert len(shutdown.calls) == 1


def test_spoken_confirmation_is_never_accepted(service, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    pending = service.execute("power_shutdown", {}, VOICE).confirmation

    result = service.confirm(pending.token, VOICE)

    assert result.status == "denied"
    assert shutdown.calls == []
    # the keyboard can still confirm it
    assert service.confirm(pending.token, LOCAL).status == "ok"


def test_confirmation_does_not_cross_between_telegram_and_local(service, monkeypatch):
    monkeypatch.setattr(system_service, "shutdown_pc", Recorder())
    tg_pending = service.execute("power_shutdown", {}, TG).confirmation
    assert service.confirm(tg_pending.token, LOCAL).status == "error"
    local_pending = service.execute("power_restart", {}, LOCAL).confirmation
    assert service.confirm(local_pending.token, TG).status == "error"


def test_telegram_confirmation_is_bound_to_the_requesting_user(service, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    pending = service.execute("power_shutdown", {}, TG).confirmation
    other_user = Actor(Channel.TELEGRAM, 222)
    assert service.confirm(pending.token, other_user).status == "error"
    assert shutdown.calls == []
    assert service.confirm(pending.token, TG).status == "ok"


def test_confirmation_expires(config, monkeypatch):
    from security.confirmations import ConfirmationStore

    now = [1000.0]
    service = ActionService(config, store=ConfirmationStore(clock=lambda: now[0]))
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    pending = service.execute("power_shutdown", {}, LOCAL).confirmation

    now[0] += config.local_confirmation_ttl_seconds + 1

    assert service.confirm(pending.token, LOCAL).status == "error"
    assert shutdown.calls == []


def test_local_code_guessing_is_locked_out(service, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    pending = service.execute("power_shutdown", {}, LOCAL).confirmation
    wrong = [f"{n:04d}" for n in range(10000) if f"{n:04d}" != pending.token][:3]
    for guess in wrong:
        service.confirm(guess, LOCAL)
    assert service.confirm(pending.token, LOCAL).status == "error"
    assert shutdown.calls == []


def test_scenario_with_require_confirmation_asks_first(service, monkeypatch, forbid_processes):
    result = service.execute("run_scenario", {"scenario_id": "work"}, LOCAL)
    assert result.status == "needs_confirmation"


def test_close_app_denied_when_not_allowed(service, forbid_processes):
    result = service.execute("close_app", {"app_id": "spotify"}, LOCAL)
    assert result.status == "error"
    assert result.code == "close_denied"


def test_power_cancel_needs_no_confirmation(service, monkeypatch):
    cancel = Recorder((True, "cancelled"))
    monkeypatch.setattr(system_service, "cancel_shutdown", cancel)
    assert service.execute("power_cancel", {}, VOICE).status == "ok"


# ---- scenarios, media, games, dry-run ----------------------------------------------


def test_gaming_scenario_runs_registered_steps(service, monkeypatch):
    opened = Recorder((True, "ok"))
    monkeypatch.setattr("services.scenarios_service.open_allowed_app", opened)
    result = service.execute("run_scenario", {"scenario_id": "gaming"}, LOCAL)
    assert result.status == "ok"
    assert [call[0][1] for call in opened.calls] == ["steam", "discord"]


def test_scenario_reports_partial_failure(service, monkeypatch):
    monkeypatch.setattr(
        "services.scenarios_service.open_allowed_app",
        lambda data_dir, key: (key == "steam", "ok" if key == "steam" else "Discord не найден"),
    )
    result = service.execute("run_scenario", {"scenario_id": "gaming"}, LOCAL)
    assert result.status == "partial"
    assert any("Discord" in line for line in result.data["failed"])


def test_media_pause_calls_media_layer(service, monkeypatch):
    media = Recorder((True, "Поставил на паузу."))
    monkeypatch.setattr(media_service, "media_control", media)
    assert service.execute("media_pause", {}, VOICE).status == "ok"
    assert media.calls == [(("pause",), {})]


def test_launch_game_uses_validated_steam_id(service, monkeypatch):
    urls = []
    monkeypatch.setattr(gaming_service.os, "startfile", urls.append, raising=False)
    assert service.execute("launch_game", {"game_id": "cs2"}, LOCAL).status == "ok"
    assert urls == ["steam://rungameid/730"]
    assert service.execute("launch_game", {"game_id": "730 & calc"}, LOCAL).status == "error"


def test_dry_run_never_touches_windows(dry_service, forbid_processes, monkeypatch):
    monkeypatch.setattr(system_service, "set_volume", Recorder())
    for action_id, params in [("open_app", {"app_id": "steam"}), ("set_volume", {"percent": 30}), ("run_scenario", {"scenario_id": "gaming"})]:
        result = dry_service.execute(action_id, params, LOCAL)
        assert result.status == "ok" and result.code == "dry_run"
    assert system_service.set_volume.calls == []


def test_dry_run_still_rejects_invalid_input(dry_service):
    assert dry_service.execute("set_volume", {"percent": 150}, LOCAL).status == "error"
    assert dry_service.execute("open_app", {"app_id": "nope"}, LOCAL).status == "error"


def test_ping_host_rejects_option_injection():
    assert system_service.ping_host("-t")[0] is False
    assert system_service.ping_host("8.8.8.8 & calc")[0] is False


def test_set_volume_service_refuses_out_of_range():
    assert system_service.set_volume(101)[0] is False
    assert system_service.set_volume(-5)[0] is False


def test_handler_exceptions_become_error_results(service, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("audio device missing")

    monkeypatch.setattr(system_service, "mute_volume", boom)
    result = service.execute("mute", {}, LOCAL)
    assert result.status == "error" and "audio device missing" in result.message
