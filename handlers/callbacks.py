from __future__ import annotations

import asyncio
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from actions import ActionService, Actor, Channel
from config import AppConfig
from handlers.dispatch import format_result, run_action, telegram_actor
from keyboards.catalogs import (
    apps_keyboard,
    media_keyboard,
    power_keyboard,
    scenarios_keyboard,
    system_keyboard,
)
from keyboards.main import main_menu_keyboard
from security.auth import ensure_authorized
from services.apps_service import load_apps
from services.scenarios_service import load_scenarios

router = Router()

MEDIA_ACTIONS = {
    "mute": "mute",
    "unmute": "unmute",
    "pause": "media_pause",
    "play": "media_play",
    "next": "media_next",
    "prev": "media_previous",
}
POWER_ACTIONS = {"shutdown": "power_shutdown", "restart": "power_restart", "cancel": "power_cancel"}
SYSTEM_ACTIONS = {"status": "system_status", "processes": "top_processes", "ping": "ping"}


@router.callback_query(F.data.startswith("menu:"))
async def menu_callback(callback: CallbackQuery, config: AppConfig, actions: ActionService) -> None:
    if not await ensure_authorized(callback, config):
        return

    action = callback.data.split(":", 1)[1]
    message = callback.message

    if action in {"main", "root"}:
        await message.answer("<b>📋 Главное меню</b>\n\nВыбери раздел ниже.", reply_markup=main_menu_keyboard())

    elif action == "system":
        await message.answer("<b>🖥 Система</b>\n\nВыбери действие.", reply_markup=system_keyboard())

    elif action == "apps":
        apps = load_apps(config.data_dir)
        if not apps:
            await message.answer("<b>📦 Приложения</b>\n\nСписок пуст.")
        else:
            await message.answer("<b>📦 Приложения</b>\n\nВыбери приложение.", reply_markup=apps_keyboard(apps))

    elif action == "scenarios":
        scenarios = load_scenarios(config.data_dir)
        if not scenarios:
            await message.answer("<b>🚀 Сценарии</b>\n\nСписок пуст.")
        else:
            await message.answer("<b>🚀 Сценарии</b>\n\nВыбери сценарий.", reply_markup=scenarios_keyboard(scenarios))

    elif action == "gaming":
        await run_action(callback, actions, "gaming_status")

    elif action == "files":
        await run_action(callback, actions, "list_files")

    elif action == "media":
        await message.answer("<b>🎵 Медиа</b>\n\nВыбери действие.", reply_markup=media_keyboard())

    elif action == "power":
        await message.answer(
            "<b>⚡ Питание</b>\n\nВыключение и перезагрузка требуют отдельного подтверждения.",
            reply_markup=power_keyboard(),
        )

    elif action == "help":
        await message.answer(
            "<b>ℹ️ Помощь</b>\n\n"
            "Это бот для быстрого управления ПК.\n"
            "Основное: система, игры, сценарии и запуск приложений."
        )

    await callback.answer()


@router.callback_query(F.data.startswith("system:"))
async def system_action_callback(callback: CallbackQuery, config: AppConfig, actions: ActionService) -> None:
    if not await ensure_authorized(callback, config):
        return

    action_id = SYSTEM_ACTIONS.get(callback.data.split(":", 1)[1])
    if action_id:
        await run_action(callback, actions, action_id)
    await callback.answer()


@router.callback_query(F.data.startswith("media:"))
async def media_action_callback(callback: CallbackQuery, config: AppConfig, actions: ActionService) -> None:
    if not await ensure_authorized(callback, config):
        return

    parts = callback.data.split(":")

    if len(parts) == 2 and parts[1] in MEDIA_ACTIONS:
        await run_action(callback, actions, MEDIA_ACTIONS[parts[1]])
    elif len(parts) == 3 and parts[1] == "vol":
        # parts[2] is validated by the action layer (integer 0..100).
        await run_action(callback, actions, "set_volume", {"percent": parts[2]})
    else:
        await callback.answer("Неизвестное действие.", show_alert=False)
        return

    await callback.answer()


@router.callback_query(F.data.startswith("app:"))
async def app_callback(callback: CallbackQuery, config: AppConfig, actions: ActionService) -> None:
    if not await ensure_authorized(callback, config):
        return

    await run_action(callback, actions, "open_app", {"app_id": callback.data.split(":", 1)[1]})
    await callback.answer()


@router.callback_query(F.data.startswith("scenario:"))
async def scenario_callback(callback: CallbackQuery, config: AppConfig, actions: ActionService) -> None:
    if not await ensure_authorized(callback, config):
        return

    await run_action(callback, actions, "run_scenario", {"scenario_id": callback.data.split(":", 1)[1]})
    await callback.answer()


@router.callback_query(F.data.startswith("power:"))
async def power_callback(callback: CallbackQuery, config: AppConfig, actions: ActionService) -> None:
    if not await ensure_authorized(callback, config):
        return

    action_id = POWER_ACTIONS.get(callback.data.split(":", 1)[1])
    if action_id:
        await run_action(callback, actions, action_id)
    await callback.answer()


async def _clear_buttons(callback: CallbackQuery) -> None:
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # message too old or already edited: not important
        pass


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_callback(callback: CallbackQuery, config: AppConfig, actions: ActionService) -> None:
    if not await ensure_authorized(callback, config):
        return

    token = callback.data.split(":", 1)[1]
    result = await asyncio.to_thread(actions.confirm, token, telegram_actor(callback))
    await _clear_buttons(callback)

    if result.code == "confirmation_expired":
        await callback.answer("Подтверждение истекло.", show_alert=True)
        return

    await callback.message.answer(format_result(result))
    await callback.answer()


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_callback(callback: CallbackQuery, config: AppConfig, actions: ActionService) -> None:
    if not await ensure_authorized(callback, config):
        return

    token = callback.data.split(":", 1)[1]
    actions.cancel(token, telegram_actor(callback))
    await _clear_buttons(callback)
    await callback.answer("Отменено.")
    await callback.message.answer("<b>❌ Действие отменено.</b>")
