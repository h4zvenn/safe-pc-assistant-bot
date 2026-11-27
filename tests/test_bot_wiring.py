"""Telegram wiring: real aiogram Dispatcher, fake network. Verifies routing, injection and auth end to end."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import patch

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from actions import ActionService
from bot import build_dispatcher
from config import AppConfig
from services import system_service
from tests.conftest import Recorder

FAKE_TOKEN = "123456789:AAHfaketokenfaketokenfaketokenfake0"
OWNER = 111


_DISPATCHER = None


def dispatcher_for(config: AppConfig, service: ActionService):
    """aiogram routers are module-level singletons: build the dispatcher once, swap its data per test."""
    global _DISPATCHER
    if _DISPATCHER is None:
        _DISPATCHER = build_dispatcher(config, service)
    _DISPATCHER["config"] = config
    _DISPATCHER["actions"] = service
    return _DISPATCHER


class Harness:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.service = ActionService(config)
        self.dp = dispatcher_for(config, self.service)
        self.sent: list[tuple[str, str, object]] = []  # (method, text, reply_markup)
        self._update_id = 0

    async def _call(self, method, request_timeout=None):
        text = getattr(method, "text", "") or ""
        self.sent.append((type(method).__name__, text, getattr(method, "reply_markup", None)))
        if isinstance(method, SendMessage):
            return Message(message_id=99, date=datetime.now(), chat=Chat(id=method.chat_id, type="private"), text=text)
        return True

    def feed(self, **payload) -> None:
        self._update_id += 1

        async def run():
            bot = Bot(FAKE_TOKEN)
            try:
                with patch.object(Bot, "__call__", self._call):
                    await self.dp.feed_update(bot, Update(update_id=self._update_id, **payload))
            finally:
                await bot.session.close()

        asyncio.run(run())

    def message(self, text: str, user_id: int = OWNER) -> None:
        self.feed(
            message=Message(
                message_id=1,
                date=datetime.now(),
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="t"),
                text=text,
                entities=[{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else None,
            )
        )

    def press(self, data: str, user_id: int = OWNER) -> None:
        self.feed(
            callback_query=CallbackQuery(
                id="cb",
                from_user=User(id=user_id, is_bot=False, first_name="t"),
                chat_instance="x",
                data=data,
                message=Message(message_id=5, date=datetime.now(), chat=Chat(id=user_id, type="private"), text="menu"),
            )
        )

    def last_message(self) -> tuple[str, str, object]:
        return [entry for entry in self.sent if entry[0] == "SendMessage"][-1]

    @property
    def texts(self) -> list[str]:
        return [text for _, text, _ in self.sent]


@pytest.fixture
def bot_env(config):
    config.authorized_user_ids = [OWNER]
    return Harness(config)


def test_authorized_start_shows_menu(bot_env):
    bot_env.message("/start")
    assert any("PC Assistant Bot" in t for t in bot_env.texts)
    assert bot_env.sent[0][2] is not None  # inline keyboard attached


def test_stranger_gets_access_denied_for_every_command(bot_env, forbid_processes):
    for command in ("/start", "/open steam", "/close steam", "/power", "/scenario gaming", "/status", "/files", "/media"):
        bot_env.sent.clear()
        bot_env.message(command, user_id=999)
        assert bot_env.texts == ["⛔ Доступ запрещён."], command


def test_open_unknown_app_replies_with_error_and_starts_nothing(bot_env, forbid_processes):
    bot_env.message("/open nonexistent")
    assert any("Неизвестное приложение" in t for t in bot_env.texts)


def test_open_missing_app_replies_with_clear_message(bot_env, forbid_processes):
    bot_env.message("/open spotify")
    assert any("Spotify не найден" in t for t in bot_env.texts)


def test_power_flow_requires_button_confirmation(bot_env, monkeypatch):
    shutdown = Recorder((True, "scheduled"))
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)

    bot_env.press("power:shutdown")
    assert shutdown.calls == []
    markup = bot_env.last_message()[2]
    confirm_data = markup.inline_keyboard[0][0].callback_data
    assert confirm_data.startswith("confirm:")

    bot_env.press(confirm_data, user_id=999)  # a stranger presses the button
    assert shutdown.calls == []

    bot_env.press(confirm_data)  # the owner confirms
    assert len(shutdown.calls) == 1

    bot_env.press(confirm_data)  # a second press must not repeat the action
    assert len(shutdown.calls) == 1


def test_cancel_button_discards_confirmation(bot_env, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    bot_env.press("power:shutdown")
    token = bot_env.last_message()[2].inline_keyboard[0][1].callback_data.split(":", 1)[1]
    bot_env.press(f"cancel:{token}")
    bot_env.press(f"confirm:{token}")
    assert shutdown.calls == []


def test_volume_buttons_validate_input(bot_env, monkeypatch):
    setter = Recorder((True, "Volume set to 50%."))
    monkeypatch.setattr(system_service, "set_volume", setter)
    bot_env.press("media:vol:50")
    bot_env.press("media:vol:500")
    bot_env.press("media:vol:evil")
    assert setter.calls == [((50,), {})]


def test_scenario_requiring_confirmation_is_confirmed_via_button(bot_env, monkeypatch):
    monkeypatch.setattr("services.scenarios_service.open_allowed_app", Recorder((True, "ok")))
    bot_env.press("scenario:work")
    _, text, markup = bot_env.last_message()
    assert markup is not None and "Подтвердить?" in text
