from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import AppConfig
from keyboards.catalogs import power_keyboard
from security.auth import ensure_authorized

router = Router()


@router.message(Command("power"))
async def cmd_power(message: Message, config: AppConfig) -> None:
    if not await ensure_authorized(message, config):
        return

    await message.answer(
        "<b>⚡ Питание</b>\n\n"
        "Выключение и перезагрузка требуют отдельного подтверждения.",
        reply_markup=power_keyboard(),
    )
