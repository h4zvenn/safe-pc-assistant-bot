from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from actions import ActionService
from config import AppConfig
from handlers.dispatch import run_action
from security.auth import ensure_authorized

router = Router()


@router.message(Command("gaming"))
async def cmd_gaming(message: Message, config: AppConfig, actions: ActionService) -> None:
    if await ensure_authorized(message, config):
        await run_action(message, actions, "gaming_status")


@router.message(Command("cs2"))
async def cmd_cs2(message: Message, config: AppConfig, actions: ActionService) -> None:
    if await ensure_authorized(message, config):
        await run_action(message, actions, "launch_game", {"game_id": "cs2"})
