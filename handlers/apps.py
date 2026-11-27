from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from actions import ActionService
from config import AppConfig
from handlers.dispatch import run_action
from security.auth import ensure_authorized

router = Router()


@router.message(Command("apps"))
async def cmd_apps(message: Message, config: AppConfig, actions: ActionService) -> None:
    if await ensure_authorized(message, config):
        await run_action(message, actions, "list_apps")


@router.message(Command("open"))
async def cmd_open(
    message: Message,
    command: CommandObject,
    config: AppConfig,
    actions: ActionService,
) -> None:
    if not await ensure_authorized(message, config):
        return

    app_key = (command.args or "").strip().lower()
    if not app_key:
        await message.answer(
            "<b>📦 Открытие приложения</b>\n\n"
            "Укажи имя приложения после команды.\n\n"
            "Пример: <code>/open steam</code>"
        )
        return

    await run_action(message, actions, "open_app", {"app_id": app_key})


@router.message(Command("close"))
async def cmd_close(
    message: Message,
    command: CommandObject,
    config: AppConfig,
    actions: ActionService,
) -> None:
    if not await ensure_authorized(message, config):
        return

    app_key = (command.args or "").strip().lower()
    if not app_key:
        await message.answer(
            "<b>📦 Закрытие приложения</b>\n\n"
            "Пример: <code>/close discord</code>\n"
            "Разрешено только для приложений с <code>allow_close: true</code> в apps.yaml."
        )
        return

    await run_action(message, actions, "close_app", {"app_id": app_key})


@router.message(Command("sites"))
async def cmd_sites(message: Message, config: AppConfig, actions: ActionService) -> None:
    if await ensure_authorized(message, config):
        await run_action(message, actions, "list_sites")


@router.message(Command("site"))
async def cmd_site(
    message: Message,
    command: CommandObject,
    config: AppConfig,
    actions: ActionService,
) -> None:
    if not await ensure_authorized(message, config):
        return

    site_key = (command.args or "").strip().lower()
    if not site_key:
        await message.answer(
            "<b>🌐 Сайты</b>\n\nПример: <code>/site youtube</code>. Список: /sites"
        )
        return

    await run_action(message, actions, "open_site", {"site_id": site_key})


@router.message(Command("runningapps"))
async def cmd_runningapps(message: Message, config: AppConfig, actions: ActionService) -> None:
    if await ensure_authorized(message, config):
        await run_action(message, actions, "running_apps")
