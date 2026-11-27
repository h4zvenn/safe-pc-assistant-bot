from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from actions import ActionService
from config import AppConfig
from handlers import apps as apps_handlers
from handlers import callbacks as callbacks_handlers
from security.auth import ensure_authorized, is_authorized
from services import system_service
from tests.conftest import Recorder


def make_config(users=(111,), chats=()) -> AppConfig:
    return AppConfig(authorized_user_ids=list(users), authorized_chat_ids=list(chats))


def make_message(user_id: int, chat_id: int | None = None, chat_type: str = "private", text: str = "/open steam") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=chat_id if chat_id is not None else user_id, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="t"),
        text=text,
    )


def make_callback(user_id: int, data: str, chat_id: int | None = None) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=user_id, is_bot=False, first_name="t"),
        chat_instance="x",
        data=data,
        message=make_message(user_id, chat_id),
    )


# ---- allowlist logic ---------------------------------------------------------------


def test_listed_user_is_authorized():
    assert is_authorized(make_config(), 111, 111)


def test_unknown_user_is_denied():
    assert not is_authorized(make_config(), 999, 999)
    assert not is_authorized(make_config(), None, 111)


def test_listed_user_in_unlisted_chat_is_denied_when_chats_are_configured():
    config = make_config(users=(111,), chats=(-100500,))
    assert is_authorized(config, 111, -100500)
    assert not is_authorized(config, 111, 111)


def test_chat_only_config_never_authorizes_group_members():
    config = make_config(users=(), chats=(-100500,))
    assert not is_authorized(config, 999, -100500)  # a stranger in the listed group
    config = make_config(users=(), chats=(111,))
    assert is_authorized(config, 111, 111)  # the listed user's private chat
    assert not is_authorized(config, 999, 111)


def test_empty_allowlists_deny_everyone():
    assert not is_authorized(make_config(users=(), chats=()), 111, 111)


# ---- aiogram events ----------------------------------------------------------------


def test_unauthorized_message_gets_denied_and_handler_does_not_run():
    config = make_config()
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        assert asyncio.run(ensure_authorized(make_message(999), config)) is False
    assert "Доступ запрещён" in answer.call_args.args[0]


def test_unauthorized_open_command_does_not_start_anything(config, forbid_processes):
    cfg = make_config()
    cfg.data_dir = config.data_dir
    service = ActionService(cfg)
    message = make_message(999)
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        from aiogram.filters import CommandObject

        asyncio.run(apps_handlers.cmd_open(message, CommandObject(command="open", args="steam"), cfg, service))
    assert "Доступ запрещён" in answer.call_args.args[0]


def test_unauthorized_callback_cannot_trigger_power_action(config, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    cfg = make_config()
    cfg.data_dir = config.data_dir
    service = ActionService(cfg)
    callback = make_callback(999, "power:shutdown")
    with patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer, patch.object(
        Message, "answer", new_callable=AsyncMock
    ) as message_answer:
        asyncio.run(callbacks_handlers.power_callback(callback, cfg, service))
    assert answer.call_args.kwargs.get("show_alert") is True
    message_answer.assert_not_called()
    assert service.store._items == {}
    assert shutdown.calls == []


def test_unauthorized_confirm_callback_cannot_consume_a_pending_confirmation(config, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    cfg = make_config()
    cfg.data_dir = config.data_dir
    service = ActionService(cfg)

    with patch.object(Message, "answer", new_callable=AsyncMock), patch.object(CallbackQuery, "answer", new_callable=AsyncMock):
        asyncio.run(callbacks_handlers.power_callback(make_callback(111, "power:shutdown"), cfg, service))
    token = next(iter(service.store._items))
    assert shutdown.calls == []  # asking is not enough

    with patch.object(CallbackQuery, "answer", new_callable=AsyncMock), patch.object(Message, "answer", new_callable=AsyncMock):
        asyncio.run(callbacks_handlers.confirm_callback(make_callback(999, f"confirm:{token}"), cfg, service))
    assert shutdown.calls == [] and token in service.store._items

    with patch.object(CallbackQuery, "answer", new_callable=AsyncMock), patch.object(
        Message, "answer", new_callable=AsyncMock
    ), patch.object(Message, "edit_reply_markup", new_callable=AsyncMock):
        asyncio.run(callbacks_handlers.confirm_callback(make_callback(111, f"confirm:{token}"), cfg, service))
    assert len(shutdown.calls) == 1


def test_forged_confirm_token_does_nothing(config, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    cfg = make_config()
    cfg.data_dir = config.data_dir
    service = ActionService(cfg)
    with patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer, patch.object(
        Message, "edit_reply_markup", new_callable=AsyncMock
    ):
        asyncio.run(callbacks_handlers.confirm_callback(make_callback(111, "confirm:shutdown"), cfg, service))
    assert shutdown.calls == []
    assert answer.called


def test_malformed_volume_callback_is_handled(config, monkeypatch):
    setter = Recorder()
    monkeypatch.setattr(system_service, "set_volume", setter)
    cfg = make_config()
    cfg.data_dir = config.data_dir
    service = ActionService(cfg)
    with patch.object(CallbackQuery, "answer", new_callable=AsyncMock), patch.object(
        Message, "answer", new_callable=AsyncMock
    ) as reply:
        asyncio.run(callbacks_handlers.media_action_callback(make_callback(111, "media:vol:9999"), cfg, service))
        asyncio.run(callbacks_handlers.media_action_callback(make_callback(111, "media:vol:abc"), cfg, service))
    assert setter.calls == []
    assert reply.call_count == 2


def test_rate_limiter_blocks_flood():
    config = make_config()
    config.rate_limit_commands_per_minute = 3
    outcomes = []
    with patch.object(Message, "answer", new_callable=AsyncMock):
        for _ in range(5):
            outcomes.append(asyncio.run(ensure_authorized(make_message(111), config)))
    assert outcomes == [True, True, True, False, False]


@pytest.mark.parametrize("data", ["menu:main", "system:status", "media:mute", "app:steam", "scenario:gaming", "power:cancel", "cancel:abc"])
def test_every_callback_handler_checks_authorization(config, forbid_processes, data):
    """No callback may reach the action layer for a stranger."""
    cfg = make_config()
    cfg.data_dir = config.data_dir
    service = ActionService(cfg)
    handler = {
        "menu": callbacks_handlers.menu_callback,
        "system": callbacks_handlers.system_action_callback,
        "media": callbacks_handlers.media_action_callback,
        "app": callbacks_handlers.app_callback,
        "scenario": callbacks_handlers.scenario_callback,
        "power": callbacks_handlers.power_callback,
        "cancel": callbacks_handlers.cancel_callback,
    }[data.split(":")[0]]
    with patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer, patch.object(
        Message, "answer", new_callable=AsyncMock
    ) as message_answer:
        asyncio.run(handler(make_callback(999, data), cfg, service))
    assert answer.call_args.kwargs.get("show_alert") is True
    message_answer.assert_not_called()
