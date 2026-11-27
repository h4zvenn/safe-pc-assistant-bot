from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from actions import ActionService, Actor, Channel
from jarvis.intent import parse, resolve_clarification
from jarvis.session import JarvisSession
from services import desktop_service, system_service
from services.config_validation import require_valid_config
from tests.conftest import Recorder
from utils.yaml_loader import ConfigError

LOCAL = Actor(Channel.LOCAL_VOICE)


def write_folders(data_dir: Path, text: str) -> None:
    (data_dir / "folders.yaml").write_text(text, encoding="utf-8")


# ---- relative volume ---------------------------------------------------------------


@pytest.mark.parametrize("current,delta,expected", [(50, 10, 60), (95, 10, 100), (5, -10, 0), (0, -30, 0), (40, -15, 25)])
def test_volume_change_is_clamped_to_0_100(service, monkeypatch, current, delta, expected):
    monkeypatch.setattr(system_service, "get_volume", lambda: (current, False))
    setter = Recorder((True, "ok"))
    monkeypatch.setattr(system_service, "set_volume", setter)
    result = service.execute("volume_change", {"delta": delta}, LOCAL)
    assert result.status == "ok" and setter.calls == [((expected,), {})]


@pytest.mark.parametrize("delta", [0, 101, -101, 1000, "abc", 2.5, None, True, [5]])
def test_volume_change_rejects_garbage(service, monkeypatch, delta):
    setter = Recorder()
    monkeypatch.setattr(system_service, "set_volume", setter)
    assert service.execute("volume_change", {"delta": delta}, LOCAL).status == "error"
    assert setter.calls == []


def test_signed_strings_are_accepted(service, monkeypatch):
    monkeypatch.setattr(system_service, "get_volume", lambda: (50, False))
    monkeypatch.setattr(system_service, "set_volume", Recorder((True, "ok")))
    assert service.execute("volume_change", {"delta": "-10"}, LOCAL).status == "ok"


# ---- desktop helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "action_id,func",
    [("lock_pc", "lock_pc"), ("show_desktop", "show_desktop"), ("screenshot", "take_screenshot")],
)
def test_desktop_actions_call_only_their_fixed_function(service, monkeypatch, action_id, func):
    spy = Recorder((True, "ok"))
    monkeypatch.setattr(desktop_service, func, spy)
    assert service.execute(action_id, {}, LOCAL).status == "ok"
    assert len(spy.calls) == 1
    assert service.execute(action_id, {"keys": "ctrl+alt+del"}, LOCAL).status == "error"


def test_dry_run_presses_no_keys(dry_service, monkeypatch):
    for name in ("lock_pc", "show_desktop", "take_screenshot"):
        monkeypatch.setattr(desktop_service, name, Recorder())
    for action_id in ("lock_pc", "show_desktop", "screenshot"):
        assert dry_service.execute(action_id, {}, LOCAL).code == "dry_run"


def test_current_time_text():
    text, data = desktop_service.current_time(dt.datetime(2026, 11, 26, 19, 5))
    assert data == {"hm": "19:05", "date": "четверг, 26 ноября"}
    assert text == "Сейчас 19:05, четверг, 26 ноября."


def test_time_is_spoken_in_both_languages(service):
    ru = JarvisSession(service).handle("который час", Channel.LOCAL_VOICE)
    uk = JarvisSession(service).handle("скільки зараз часу", Channel.LOCAL_VOICE)
    assert ru.text.startswith("Сейчас ") and uk.text.startswith("Зараз ")


# ---- folders -----------------------------------------------------------------------


def test_open_folder_uses_only_the_configured_directory(service, data_dir, tmp_path, monkeypatch):
    target = tmp_path / "Downloads"
    target.mkdir()
    monkeypatch.setenv("JARVIS_TEST_HOME", str(tmp_path))
    write_folders(
        data_dir,
        'shortcuts:\n  downloads:\n    name: "Загрузки"\n    path: "%JARVIS_TEST_HOME%/Downloads"\n    aliases: ["загрузки"]\n',
    )
    opened = []
    monkeypatch.setattr(desktop_service.os, "startfile", opened.append, raising=False)
    assert service.execute("open_folder", {"folder_id": "downloads"}, LOCAL).status == "ok"
    assert opened == [str(target)]


@pytest.mark.parametrize("folder_id", ["C:/Windows", "../..", "downloads; calc", "nope", "", None])
def test_open_folder_rejects_paths_and_unknown_ids(service, data_dir, monkeypatch, folder_id):
    write_folders(data_dir, 'shortcuts:\n  downloads:\n    path: "C:/x"\n')
    monkeypatch.setattr(desktop_service.os, "startfile", lambda p: pytest.fail("must not open"), raising=False)
    assert service.execute("open_folder", {"folder_id": folder_id}, LOCAL).status == "error"


def test_missing_folder_gives_clear_error(service, data_dir, tmp_path, monkeypatch):
    write_folders(data_dir, f'shortcuts:\n  downloads:\n    name: "Загрузки"\n    path: "{(tmp_path / "gone").as_posix()}"\n')
    result = service.execute("open_folder", {"folder_id": "downloads"}, LOCAL)
    assert result.status == "error" and "не найдена" in result.message


def test_shortcut_validation(data_dir):
    write_folders(data_dir, "shortcuts:\n  'bad id!':\n    path: 'C:/x'\n")
    with pytest.raises(ConfigError, match="id must match"):
        require_valid_config(data_dir)
    write_folders(data_dir, "shortcuts:\n  ok:\n    name: x\n")
    with pytest.raises(ConfigError, match="path"):
        require_valid_config(data_dir)


# ---- timers ------------------------------------------------------------------------


class FakeTimer:
    created: list["FakeTimer"] = []

    def __init__(self, interval, function, args=()):
        self.interval, self.function, self.args = interval, function, args
        self.daemon = False
        self.cancelled = False
        self.started = False
        FakeTimer.created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def is_alive(self):
        return self.started and not self.cancelled

    def fire(self):
        self.function(*self.args)


@pytest.fixture
def timers(monkeypatch):
    FakeTimer.created = []
    monkeypatch.setattr("actions.service.threading.Timer", FakeTimer)
    return FakeTimer.created


def test_timer_needs_somewhere_to_announce(service, timers):
    result = service.execute("set_timer", {"minutes": 5}, LOCAL)
    assert result.status == "error" and result.code == "timer_unavailable" and timers == []


def test_timer_fires_through_the_notifier(service, timers):
    said = []
    service.notifier = said.append
    assert service.execute("set_timer", {"minutes": 5}, LOCAL).status == "ok"
    assert timers[0].interval == 300 and timers[0].daemon and timers[0].started
    timers[0].fire()
    assert said == ["Таймер на 5 мин сработал."]


def test_broken_notifier_does_not_crash_the_timer_thread(service, timers):
    def boom(_):
        raise RuntimeError("no sound device")

    service.notifier = boom
    service.execute("set_timer", {"minutes": 1}, LOCAL)
    timers[0].fire()  # must not raise


@pytest.mark.parametrize("minutes", [0, -5, 721, "abc", 1.5, None, True])
def test_timer_bounds(service, timers, minutes):
    service.notifier = lambda _: None
    assert service.execute("set_timer", {"minutes": minutes}, LOCAL).status == "error"
    assert timers == []


def test_cancel_timers(service, timers):
    service.notifier = lambda _: None
    service.execute("set_timer", {"minutes": 5}, LOCAL)
    service.execute("set_timer", {"minutes": 10}, LOCAL)
    assert service.execute("cancel_timers", {}, LOCAL).code == "timer_cancelled"
    assert all(t.cancelled for t in timers)
    assert service.execute("cancel_timers", {}, LOCAL).code == "timer_none"


def test_dry_run_starts_no_timer(dry_service, timers):
    dry_service.notifier = lambda _: None
    assert dry_service.execute("set_timer", {"minutes": 5}, LOCAL).code == "dry_run"
    assert timers == []


# ---- phrases -----------------------------------------------------------------------


def intent_of(service, text):
    parsed = parse(text, service.catalog())
    assert parsed.intent is not None, f"{text!r}: {parsed}"
    return parsed.intent.action_id, parsed.intent.params


@pytest.mark.parametrize(
    "text,expected",
    [
        ("сделай потише", ("volume_change", {"delta": -10})),
        ("громче", ("volume_change", {"delta": 10})),
        ("прибавь громкость на 20", ("volume_change", {"delta": 20})),
        ("убавь звук", ("volume_change", {"delta": -10})),
        ("тише на пятнадцать", ("volume_change", {"delta": -15})),
        ("гучніше", ("volume_change", {"delta": 10})),
        ("громкость 30 процентов", ("set_volume", {"percent": 30})),  # absolute stays absolute
        ("заблокируй компьютер", ("lock_pc", {})),
        ("заблокуй екран", ("lock_pc", {})),
        ("который час", ("current_time", {})),
        ("какое сегодня число", ("current_time", {})),
        ("сверни все окна", ("show_desktop", {})),
        ("покажи рабочий стол", ("show_desktop", {})),
        ("сделай скриншот", ("screenshot", {})),
        ("поставь таймер на 5 минут", ("set_timer", {"minutes": 5})),
        ("напомни через полчаса", ("set_timer", {"minutes": 30})),
        ("таймер на час", ("set_timer", {"minutes": 60})),
        ("таймер на два часа", ("set_timer", {"minutes": 120})),
        ("постав таймер на десять хвилин", ("set_timer", {"minutes": 10})),
        ("отмени таймер", ("cancel_timers", {})),
    ],
)
def test_new_phrases(service, text, expected):
    assert intent_of(service, text) == expected


def test_timer_without_a_time_asks_for_it(service):
    parsed = parse("поставь таймер", service.catalog())
    assert parsed.clarification is not None and parsed.clarification.kind == "timer"
    assert resolve_clarification("на семь минут", parsed.clarification).intent.params == {"minutes": 7}


def test_folder_phrases_and_the_downloads_regression(service, data_dir):
    write_folders(
        data_dir,
        'shortcuts:\n  downloads:\n    name: "Загрузки"\n    path: "C:/x"\n    aliases: ["загрузки", "завантаження"]\n',
    )
    assert intent_of(service, "открой загрузки") == ("open_folder", {"folder_id": "downloads"})  # was "system_status"
    assert intent_of(service, "відкрий завантаження") == ("open_folder", {"folder_id": "downloads"})
    assert intent_of(service, "покажи состояние пк")[0] == "system_status"


def test_ordinary_phrases_still_work(service):
    assert intent_of(service, "открой steam")[0] == "open_app"
    assert intent_of(service, "включи музыку")[0] == "media_play"
    assert intent_of(service, "выключи звук")[0] == "mute"
