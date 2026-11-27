from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from actions import ActionService
from config import AppConfig
from handlers.dispatch import run_action
from security.auth import ensure_authorized

router = Router()


@router.message(Command("scenarios"))
async def cmd_scenarios(message: Message, config: AppConfig, actions: ActionService) -> None:
    if await ensure_authorized(message, config):
        await run_action(message, actions, "list_scenarios")


@router.message(Command("scenario"))
async def cmd_scenario(
    message: Message,
    command: CommandObject,
    config: AppConfig,
    actions: ActionService,
) -> None:
    if not await ensure_authorized(message, config):
        return

    scenario_key = (command.args or "").strip().lower()
    if not scenario_key:
        await message.answer(
            "<b>🚀 Сценарии</b>\n\nИспользование: <code>/scenario &lt;имя&gt;</code>"
        )
        return

    await run_action(message, actions, "run_scenario", {"scenario_id": scenario_key})
