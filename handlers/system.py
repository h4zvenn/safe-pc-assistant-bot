from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from actions import ActionService
from config import AppConfig
from handlers.dispatch import run_action
from security.auth import ensure_authorized

router = Router()


@router.message(Command("status"))
async def cmd_status(message: Message, config: AppConfig, actions: ActionService) -> None:
    if await ensure_authorized(message, config):
        await run_action(message, actions, "system_status")


@router.message(Command("processes"))
async def cmd_processes(message: Message, config: AppConfig, actions: ActionService) -> None:
    if await ensure_authorized(message, config):
        await run_action(message, actions, "top_processes")


@router.message(Command("ping"))
async def cmd_ping(message: Message, config: AppConfig, actions: ActionService) -> None:
    if await ensure_authorized(message, config):
        await run_action(message, actions, "ping")
