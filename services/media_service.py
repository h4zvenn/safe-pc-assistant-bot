from __future__ import annotations

import asyncio
import ctypes
from typing import Literal

MediaCommand = Literal["pause", "play", "next", "previous"]

_PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
_PAUSED = 5

_VK_MEDIA = {"play": 0xB3, "pause": 0xB3, "next": 0xB0, "previous": 0xB1}
_KEYEVENTF_KEYUP = 0x0002


async def _smtc(command: MediaCommand) -> tuple[bool, str]:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    )

    manager = await SessionManager.request_async()
    session = manager.get_current_session()
    if session is None:
        return False, "Сейчас нет активного медиаплеера."

    status = session.get_playback_info().playback_status
    if command == "pause":
        if status != _PLAYING:
            return True, "Музыка уже на паузе."
        done = await session.try_pause_async()
        return bool(done), "Поставил на паузу." if done else "Плеер не принял команду паузы."
    if command == "play":
        if status == _PLAYING:
            return True, "Музыка уже играет."
        done = await session.try_play_async()
        return bool(done), "Продолжаю воспроизведение." if done else "Плеер не принял команду."
    if command == "next":
        done = await session.try_skip_next_async()
        return bool(done), "Следующий трек." if done else "Плеер не принял команду."
    done = await session.try_skip_previous_async()
    return bool(done), "Предыдущий трек." if done else "Плеер не принял команду."


def _media_key(command: MediaCommand) -> tuple[bool, str]:
    # Fallback without the Windows media session API: a fixed media key press.
    # Play/pause is a toggle here, so the result is worded accordingly.
    code = _VK_MEDIA[command]
    user32 = ctypes.windll.user32
    user32.keybd_event(code, 0, 0, 0)
    user32.keybd_event(code, 0, _KEYEVENTF_KEYUP, 0)
    return True, "Отправил медиаклавишу (пауза/пуск переключаются)."


def media_control(command: MediaCommand) -> tuple[bool, str]:
    if command not in _VK_MEDIA:
        return False, "Неизвестная медиакоманда."
    try:
        return asyncio.run(_smtc(command))
    except ImportError:
        return _media_key(command)
    except Exception as exc:
        return False, f"Медиа: ошибка управления плеером: {exc}"
