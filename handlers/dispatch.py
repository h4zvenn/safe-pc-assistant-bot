from __future__ import annotations

import asyncio
from html import escape
from typing import Any

from aiogram.types import CallbackQuery, Message

from actions import ActionResult, ActionService, Actor, Channel
from keyboards.confirmations import confirm_keyboard

_ICONS = {"ok": "✅", "partial": "⚠️", "error": "❌", "denied": "⛔"}


def telegram_actor(event: Message | CallbackQuery) -> Actor:
    user = event.from_user
    return Actor(Channel.TELEGRAM, user.id if user else 0)


def format_result(result: ActionResult) -> str:
    title = f"<b>{escape(result.title)}</b>\n\n" if result.title else ""
    if result.status == "needs_confirmation":
        return f"{title}⚠️ {escape(result.message)}\n\nПодтвердить?"
    icon = _ICONS.get(result.status, "")
    return f"{title}{icon} {escape(result.message)}"


async def send_result(message: Message, result: ActionResult) -> None:
    if result.status == "needs_confirmation" and result.confirmation:
        await message.answer(format_result(result), reply_markup=confirm_keyboard(result.confirmation.token))
    else:
        await message.answer(format_result(result))


async def run_action(
    event: Message | CallbackQuery,
    actions: ActionService,
    action_id: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Run a registered action off the event loop and answer in the chat."""
    target = event if isinstance(event, Message) else event.message
    result = await asyncio.to_thread(actions.execute, action_id, params, telegram_actor(event))
    await send_result(target, result)
