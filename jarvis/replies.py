"""Short spoken / printed replies in Russian or Ukrainian, built from an action result."""

from __future__ import annotations

from actions.models import ActionResult

_T: dict[str, dict[str, str]] = {
    "app_opened": {"ru": "{label} открыт.", "uk": "{label} відкрито."},
    "app_failed": {
        "ru": "Не удалось открыть {label}. Возможно, он не установлен.",
        "uk": "Не вдалося відкрити {label}. Можливо, його не встановлено.",
    },
    "app_closed": {"ru": "{label} закрыт.", "uk": "{label} закрито."},
    "app_close_failed": {"ru": "{label} не закрыт: он не запущен.", "uk": "{label} не закрито: він не запущений."},
    "game_launched": {"ru": "Запускаю {label}.", "uk": "Запускаю {label}."},
    "game_failed": {"ru": "Не удалось запустить {label}.", "uk": "Не вдалося запустити {label}."},
    "site_opened": {"ru": "Открываю {label}.", "uk": "Відкриваю {label}."},
    "site_searched": {"ru": "Ищу на {label}.", "uk": "Шукаю на {label}."},
    "site_failed": {"ru": "Не удалось открыть {label}.", "uk": "Не вдалося відкрити {label}."},
    "missing_param": {
        "ru": "Не понял, что именно открыть или сделать. Назови точнее.",
        "uk": "Не зрозумів, що саме відкрити чи зробити. Назви точніше.",
    },
    "unknown_site": {"ru": "Такого сайта нет в списке.", "uk": "Такого сайту немає у списку."},
    "locked": {"ru": "Экран заблокирован.", "uk": "Екран заблоковано."},
    "desktop_shown": {"ru": "Окна свёрнуты.", "uk": "Вікна згорнуто."},
    "screenshot_taken": {"ru": "Скриншот сделан.", "uk": "Скріншот зроблено."},
    "current_time": {"ru": "Сейчас {hm}, {date}.", "uk": "Зараз {hm}."},
    "folder_opened": {"ru": "Открываю папку {label}.", "uk": "Відкриваю папку {label}."},
    "folder_failed": {"ru": "Не удалось открыть папку {label}.", "uk": "Не вдалося відкрити папку {label}."},
    "timer_set": {"ru": "Таймер на {minutes} минут запущен.", "uk": "Таймер на {minutes} хвилин запущено."},
    "timer_cancelled": {"ru": "Таймеры отменены.", "uk": "Таймери скасовано."},
    "timer_none": {"ru": "Активных таймеров нет.", "uk": "Активних таймерів немає."},
    "timer_unavailable": {"ru": "Таймеры работают только в режиме JARVIS.", "uk": "Таймери працюють лише в режимі JARVIS."},
    "volume_set": {"ru": "Громкость {percent} процентов.", "uk": "Гучність {percent} відсотків."},
    "muted": {"ru": "Звук выключен.", "uk": "Звук вимкнено."},
    "unmuted": {"ru": "Звук включён.", "uk": "Звук увімкнено."},
    "volume_failed": {"ru": "Не удалось изменить громкость.", "uk": "Не вдалося змінити гучність."},
    "media_failed": {"ru": "Не удалось управлять плеером.", "uk": "Не вдалося керувати плеєром."},
    "system_status": {
        "ru": "Процессор {cpu} процентов, память {ram} процентов, диск {disk} процентов.",
        "uk": "Процесор {cpu} відсотків, пам'ять {ram} відсотків, диск {disk} відсотків.",
    },
    "scenario_ok": {"ru": "Сценарий {label} выполнен.", "uk": "Сценарій {label} виконано."},
    "scenario_partial": {
        "ru": "Сценарий {label} выполнен частично, смотри подробности.",
        "uk": "Сценарій {label} виконано частково, дивись подробиці.",
    },
    "scenario_error": {"ru": "Сценарий {label} не выполнен.", "uk": "Сценарій {label} не виконано."},
    "needs_confirmation": {
        "ru": "Нужно подтверждение с клавиатуры.",
        "uk": "Потрібне підтвердження з клавіатури.",
    },
    "voice_confirm": {
        "ru": "Голосом подтвердить нельзя.",
        "uk": "Голосом підтвердити не можна.",
    },
    "confirmation_expired": {"ru": "Подтверждение истекло.", "uk": "Підтвердження закінчилось."},
    "cancelled": {"ru": "Отменено.", "uk": "Скасовано."},
    "power_shutdown": {"ru": "Выключение запланировано.", "uk": "Вимкнення заплановано."},
    "power_restart": {"ru": "Перезагрузка запланирована.", "uk": "Перезавантаження заплановано."},
    "power_cancel": {"ru": "Выключение отменено.", "uk": "Вимкнення скасовано."},
    "unknown_app": {"ru": "Такого приложения нет в списке.", "uk": "Такого застосунку немає у списку."},
    "unknown_scenario": {"ru": "Такого сценария нет.", "uk": "Такого сценарію немає."},
    "out_of_range": {"ru": "Значение вне допустимого диапазона.", "uk": "Значення поза допустимим діапазоном."},
    "close_denied": {"ru": "Закрывать это приложение нельзя.", "uk": "Закривати цей застосунок не можна."},
    "unknown_action": {"ru": "Такого действия нет.", "uk": "Такої дії немає."},
    "dry_run": {"ru": "Проверка без выполнения.", "uk": "Перевірка без виконання."},
    "ping_ok": {"ru": "Сеть работает.", "uk": "Мережа працює."},
    "ping_failed": {"ru": "Нет ответа от сети.", "uk": "Немає відповіді від мережі."},
    "media_pause": {"uk": "Поставив на паузу."},
    "media_play": {"uk": "Продовжую відтворення."},
    "media_next": {"uk": "Наступний трек."},
    "media_previous": {"uk": "Попередній трек."},
}
_GENERIC = {
    "ok": {"ru": "Готово.", "uk": "Готово."},
    "partial": {"ru": "Выполнено частично.", "uk": "Виконано частково."},
    "error": {"ru": "Не получилось.", "uk": "Не вийшло."},
    "denied": {"ru": "Это действие запрещено.", "uk": "Ця дія заборонена."},
}
UNKNOWN = {"ru": "Не понял команду.", "uk": "Не зрозумів команду."}


def reply_for(result: ActionResult, lang: str, label: str = "") -> str:
    lang = lang if lang in ("ru", "uk") else "ru"
    if lang == "ru" and result.code.startswith("media_") and result.code != "media_failed":
        return result.message  # already short Russian: "Поставил на паузу." / "Музыка уже на паузе."
    template = _T.get(result.code, {}).get(lang)
    if template is None:
        return _GENERIC.get(result.status, _GENERIC["ok"])[lang]
    data = result.data
    return template.format(
        label=label,
        percent=data.get("percent", ""),
        cpu=round(data.get("cpu_percent", 0)),
        ram=round(data.get("memory_percent", 0)),
        disk=round(data.get("disk_percent", 0)),
        hm=data.get("hm", ""),
        date=data.get("date", ""),
        minutes=data.get("minutes", ""),
    )
