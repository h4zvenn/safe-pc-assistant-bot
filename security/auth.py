from __future__ import annotations

import logging

from aiogram.types import CallbackQuery, Message

from config import AppConfig
from security.rate_limit import RateLimiter

_limiters: dict[int, tuple[AppConfig, RateLimiter]] = {}


def is_authorized(config: AppConfig, user_id: int | None, chat_id: int | None) -> bool:
    """Telegram allowlist check.

    With AUTHORIZED_USER_IDS set, the sender must be listed (and, if AUTHORIZED_CHAT_IDS
    is set too, the chat must be listed). With only AUTHORIZED_CHAT_IDS, access is limited
    to those users' private chats, so a listed group never grants access to its members.
    """
    if user_id is None or chat_id is None:
        return False

    if config.authorized_user_ids:
        if user_id not in config.authorized_user_ids:
            return False
        return not config.authorized_chat_ids or chat_id in config.authorized_chat_ids

    return user_id in config.authorized_chat_ids and chat_id == user_id


def _limiter_for(config: AppConfig) -> RateLimiter:
    # The entry keeps a reference to the config, so its id() cannot be reused by another object.
    entry = _limiters.get(id(config))
    if entry is None or entry[0] is not config:
        entry = (config, RateLimiter(config.rate_limit_commands_per_minute))
        _limiters[id(config)] = entry
    return entry[1]


async def ensure_authorized(event: Message | CallbackQuery, config: AppConfig) -> bool:
    user_id = event.from_user.id if event.from_user else None

    if isinstance(event, CallbackQuery):
        if not isinstance(event.message, Message):
            await event.answer("Сообщение устарело, открой меню заново.", show_alert=True)
            return False
        chat_id = event.message.chat.id
    else:
        chat_id = event.chat.id

    if not is_authorized(config, user_id, chat_id):
        logging.warning("Unauthorized Telegram access: user_id=%s chat_id=%s", user_id, chat_id)
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Доступ запрещён", show_alert=True)
        else:
            await event.answer("⛔ Доступ запрещён.")
        return False

    if not _limiter_for(config).hit(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("Слишком много команд, подожди минуту.", show_alert=True)
        else:
            await event.answer("⏳ Слишком много команд, подожди минуту.")
        return False

    return True
