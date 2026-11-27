"""Rule-based parsing of Russian / Ukrainian commands into registered actions.

Input is untrusted text (keyboard, microphone, or an LLM answer). The output is only an
action id plus typed parameters; ActionService validates them again before anything runs.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from actions.service import Catalog

# ---- normalisation -----------------------------------------------------------------

_FOLD = str.maketrans(
    {"ё": "е", "і": "и", "ї": "и", "є": "е", "ґ": "г", "ь": "", "ъ": "", "'": "", "’": "", "ʼ": "", "`": ""}
)


def normalize(text: str) -> str:
    """Lower-case, fold Ukrainian/Russian letter differences, drop punctuation."""
    folded = text.lower().translate(_FOLD)
    folded = re.sub(r"[^\w%]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


_FILLERS = [normalize(w) for w in ("пожалуйста", "будь ласка", "джарвис", "джарвіс", "жарвис", "jarvis", "слушай", "слухай")]


def _strip_fillers(text: str) -> str:
    for filler in sorted(_FILLERS, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(filler)}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def guess_language(text: str) -> str:
    """'uk' when the text contains letters or words that only Ukrainian uses, else 'ru'."""
    lowered = text.lower()
    if re.search(r"[іїєґ]", lowered):
        return "uk"
    if re.search(r"\b(відкрий|гучність|відсотків|будь ласка)\b", lowered):
        return "uk"
    return "ru"


# ---- numbers -----------------------------------------------------------------------

_NUMBER_WORDS: dict[str, int] = {}
for _value, _words in {
    0: "ноль нуль",
    1: "один одна одну",
    2: "два две дві",
    3: "три",
    4: "четыре чотири",
    5: "пять п'ять",
    6: "шесть шість",
    7: "семь сім",
    8: "восемь вісім",
    9: "девять дев'ять",
    10: "десять",
    11: "одиннадцать одинадцять",
    12: "двенадцать дванадцять",
    13: "тринадцать тринадцять",
    14: "четырнадцать чотирнадцять",
    15: "пятнадцать п'ятнадцять",
    16: "шестнадцать шістнадцять",
    17: "семнадцать сімнадцять",
    18: "восемнадцать вісімнадцять",
    19: "девятнадцать дев'ятнадцять",
    20: "двадцать двадцять",
    30: "тридцать тридцять",
    40: "сорок",
    50: "пятьдесят п'ятдесят",
    60: "шестьдесят шістдесят",
    70: "семьдесят сімдесят",
    80: "восемьдесят вісімдесят",
    90: "девяносто дев'яносто",
    100: "сто",
    200: "двести двісті",
    300: "триста",
}.items():
    for _word in _words.split():
        _NUMBER_WORDS[normalize(_word)] = _value
# "на половину" / "на максимум"
_NUMBER_WORDS.update({normalize("половину"): 50, normalize("половина"): 50, normalize("максимум"): 100})


def extract_number(text: str) -> int | None:
    """First number in the text: digits ('30', '30%') or words ('тридцать', 'сто пятьдесят')."""
    tokens = normalize(text).replace("%", " % ").split()
    for index, token in enumerate(tokens):
        if token.isdigit() and len(token) <= 4:
            return int(token)
        if token not in _NUMBER_WORDS:
            continue
        total = last = _NUMBER_WORDS[token]
        for follower in tokens[index + 1 :]:
            value = _NUMBER_WORDS.get(follower)
            if value is None:
                break
            hundreds_then_less = last >= 100 and value < 100
            tens_then_unit = 20 <= last < 100 and last % 10 == 0 and 1 <= value <= 9
            if not (hundreds_then_less or tens_then_unit):
                break
            total += value
            last = value
        return total
    return None


# ---- result types ------------------------------------------------------------------


@dataclass(slots=True)
class Intent:
    action_id: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "rules"


@dataclass(slots=True)
class Option:
    label: str
    intent: Intent
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Clarification:
    kind: str  # "choice" (pick one of options) or "percent" (give a volume number)
    question: str
    options: list[Option] = field(default_factory=list)


@dataclass(slots=True)
class ParseResult:
    intent: Intent | None = None
    clarification: Clarification | None = None
    unknown: bool = False
    language: str = "ru"
    chat_reply: str | None = None  # plain conversational answer from the LLM, never an action


# ---- vocabulary --------------------------------------------------------------------

_OPEN = re.compile(r"\b(откр\w*|видкр\w*|запуск\w*|запуст\w*|включ\w*|увимкн\w*|вмик\w*|зайд\w*|зайт\w*|перейд\w*|перейт\w*|стартуи\w*|старт|open|launch|start|run)\b")
_CLOSE = re.compile(r"\b(закр\w*|закри\w*|выключ\w*|вимкн\w*|заверш\w*|убеи|останов\w*|close|kill)\b")

_POWER_CANCEL = re.compile(r"\b(отмен\w*|скасу\w*|отбои|cancel|abort)\b.*\b(выключ\w*|вимкн\w*|перезагруз\w*|перезавантаж\w*|завершен\w*|питан\w*)")
_POWER_OBJECT = r"(пк|комп\w*|ноутбук\w*|систем\w*|windows|виндовс|питан\w*|работ\w*|робот\w*|машин\w*)"
_SHUTDOWN = re.compile(rf"\b(выключ\w*|вимкн\w*|отключ\w*|заверш\w*|вырубь|shutdown)\b\s+(?:мои\s+|мии\s+|весь\s+|вес\s+|сво\w*\s+)?{_POWER_OBJECT}\b|\bshut\s*down\b")
_RESTART = re.compile(r"\b(перезагруз\w*|перезавантаж\w*|рестарт\w*|restart|reboot)\b")

_UNMUTE = re.compile(r"\b(включ\w*|увимкн\w*|вмик\w*|верн\w*|размут\w*)\b\s+(?:весь\s+)?(звук\w*|громк\w*|гуч\w*)|\bunmute\b")
_MUTE = re.compile(r"\b(выключ\w*|вимкн\w*|отключ\w*|заглуш\w*|убери|убрать|замут\w*|mute)\b\s+(?:весь\s+)?(звук\w*|громк\w*|гуч\w*)|\bбез звук\w*|\bмут\b|\bmute\b")

_PLAY_FROM_PAUSE = re.compile(r"\b(сним\w*|зним\w*)\s+(с|з)\s+пауз\w*")


def _phrases(*items: str) -> re.Pattern[str]:
    """Whole-phrase matcher over already-normalised text (so ru/uk letter folding applies)."""
    return re.compile(r"\b(" + "|".join(re.escape(normalize(item)) for item in items) + r")\b")


_TIME_Q = _phrases(
    "который час", "сколько времени", "сколько сейчас времени", "скажи время", "время сейчас", "какое сегодня число",
    "какой сегодня день", "какая сегодня дата", "скільки часу", "скільки зараз часу", "котра година",
    "яке сьогодні число", "яка сьогодні дата", "скажи час",
)
_LOCK = _phrases("заблокируй", "заблокировать", "заблокуй", "заблокувати", "залочь", "lock screen", "lock pc")
_DESKTOP = _phrases(
    "покажи рабочий стол", "покажи робочий стіл", "сверни все", "сверни окна", "сверни все окна", "згорни все",
    "згорни вікна", "рабочий стол", "робочий стіл",
)
_SCREENSHOT = _phrases("скриншот", "сделай скрин", "снимок экрана", "скріншот", "зроби скрін", "знімок екрана")
_LOUDER = re.compile(r"\b(громч\w*|погромч\w*|гучнише|погучнише)\b")
_QUIETER = re.compile(r"\b(тише|потише|тихише|потихише)\b")
_LOUDER_VERB = re.compile(r"\b(прибав\w*|увелич\w*|збилш\w*|додаи)\b")
_QUIETER_VERB = re.compile(r"\b(убав\w*|умен\w*|зменш\w*|приглуш\w*|зниз\w*)\b")
_TIMER = re.compile(r"\b(таймер\w*|напомн\w*|нагадаи\w*|нагад\w*|будильник\w*)\b")
_TIMER_CANCEL = re.compile(r"\b(отмен\w*|скасу\w*|сброс\w*|останов\w*|убер\w*)\b.*\b(таймер\w*|напомин\w*|нагад\w*)")
_HOUR = re.compile(r"\b(час\w*|годин\w*|hour\w*)\b")
_HALF_HOUR = re.compile(r"\b(полчаса|півгодини|пивгодини)\b")
_MEDIA_PAUSE = re.compile(r"\bпауз\w*|\bpause\b|\b(останов\w*|зупин\w*|стоп|stop)\s+(музык\w*|музик\w*|трек\w*|песн\w*|писн\w*|плеер\w*|плеєр\w*|воспроизвед\w*)")
_MEDIA_PLAY = re.compile(r"\b(продолж\w*|продовж\w*|возобнов\w*|поновит\w*|play)\b|\b(включ\w*|увимкн\w*|вмик\w*|запуст\w*|запуск\w*)\s+(музык\w*|музик\w*|трек\w*|песн\w*|писн\w*|плеер\w*|плеєр\w*)")
_MEDIA_NEXT = re.compile(r"\b(следующ\w*|наступн\w*|next|дальше|далии|пропуст\w*)\b")
_MEDIA_PREV = re.compile(r"\b(предыдущ\w*|попередн\w*|previous|prev)\b|\bназад\s+(трек\w*|песн\w*|писн\w*|музык\w*)")

_VOLUME_WORD = re.compile(r"\b(громк\w*|гуч\w*|volume|звук\w*)\b")

_GAMING_STATUS = re.compile(r"\b(игров\w*|ігров\w*)\s+статус\w*|\bстатус\w*\s+(игр\w*|ігор|ігр\w*)")
_RUNNING_APPS = re.compile(r"\bчто\s+запущен\w*|\bщо\s+запущен\w*|\bзапущен\w*\s+(приложен\w*|програм\w*)|\b(какие|яки\w*|які)\s+(приложен\w*|програм\w*)\s+(запущен\w*|работа\w*|працю\w*)")
_LIST_APPS = re.compile(r"\b(список|перечен\w*|перелик)\s+(приложен\w*|програм\w*)|\b(какие|які)\s+(приложен\w*|програм\w*)\s+(есть|доступн\w*|мае\w*)")
_LIST_SCENARIOS = re.compile(r"\b(список|перечен\w*|перелик|какие|які)\s+(сценари\w*|режим\w*)")
_PROCESSES = re.compile(r"\bпроцесс\w*|\bпроцес\w*|\bчто\s+грузит|\bщо\s+вантаж\w*|\bщо\s+навантаж\w*|\bтяжел\w*\s+(приложен\w*|процесс\w*)|\bтоп\s+процесс\w*")
_PING = re.compile(r"\bпинг\w*|\bping\b|\b(интернет|интернет\w*|инет|мереж\w*|сеть|сети)\b")
_STATUS = re.compile(r"\b(состояни\w*|стан|статус\w*|нагрузк\w*|навантаж\w*|status)\b|\bзагрузк\w*\s+(цп|процессор\w*|системы|пк|комп\w*)\b|\b(как|яки|як)\s+(дела|справи|там)\s+(у|на|с|в)?\s*(пк|комп\w*|систем\w*|ноутбук\w*|машин\w*)\b")

_GENERIC_GAME = re.compile(r"\b(игр[уаы]|игры|гру|игра|game|games)\b")

_MAX_CLARIFY_OPTIONS = 6

_SEARCH = re.compile(r"\b(найд\w*|найт\w*|поищ\w*|поиск\w*|знайд\w*|пошук\w*|шука\w*|search)\b")
_SEARCH_NOISE = re.compile(r"(найд\w*|найт\w*|поищ\w*|поиск\w*|знайд\w*|пошук\w*|шука\w*|search|покаж\w*)")
_SEARCH_STOP = {normalize(w) for w in ("в", "во", "на", "у", "по", "через", "мне", "нам", "там", "пожалуйста")}
_DEFAULT_SEARCH_SITE = "google"


# ---- entity matching ---------------------------------------------------------------


@dataclass(slots=True)
class _Entity:
    kind: str  # "app" | "scenario" | "game"
    key: str
    label: str
    aliases: list[str]


def _entities(catalog: Catalog) -> list[_Entity]:
    result: list[_Entity] = []
    for kind, group in (
        ("app", catalog.apps), ("scenario", catalog.scenarios), ("game", catalog.games),
        ("site", catalog.sites), ("folder", catalog.folders),
    ):
        for key, (label, aliases) in group.items():
            names = {normalize(a) for a in [*aliases, label, key.replace("_", " ")] if a}
            result.append(_Entity(kind, key, label, sorted(n for n in names if n)))
    return result


def _find_exact(tokens: list[str], alias: str) -> bool:
    parts = alias.split()
    size = len(parts)
    return any(tokens[i : i + size] == parts for i in range(len(tokens) - size + 1))


def _fuzzy_score(tokens: list[str], alias: str) -> float:
    parts = alias.split()
    size = len(parts)
    if len(alias) < 5:
        return 0.0
    best = 0.0
    for i in range(len(tokens) - size + 1):
        window = " ".join(tokens[i : i + size])
        best = max(best, difflib.SequenceMatcher(None, window, alias).ratio())
    return best


def _match_entities(text: str, entities: list[_Entity]) -> tuple[list[_Entity], list[tuple[float, _Entity]]]:
    """Return (exact matches, fuzzy candidates sorted best first)."""
    tokens = text.split()
    exact: list[_Entity] = []
    fuzzy: list[tuple[float, _Entity]] = []
    for entity in entities:
        if any(_find_exact(tokens, alias) for alias in entity.aliases):
            exact.append(entity)
            continue
        score = max((_fuzzy_score(tokens, alias) for alias in entity.aliases), default=0.0)
        if score >= 0.7:
            fuzzy.append((score, entity))
    fuzzy.sort(key=lambda item: item[0], reverse=True)
    return exact, fuzzy


def _intent_for(entity: _Entity, closing: bool = False) -> Intent:
    if entity.kind == "app":
        return Intent("close_app" if closing else "open_app", {"app_id": entity.key})
    if entity.kind == "scenario":
        return Intent("run_scenario", {"scenario_id": entity.key})
    if entity.kind == "site":
        return Intent("open_site", {"site_id": entity.key})
    if entity.kind == "folder":
        return Intent("open_folder", {"folder_id": entity.key})
    return Intent("launch_game", {"game_id": entity.key})


def _option(entity: _Entity, closing: bool = False) -> Option:
    return Option(entity.label, _intent_for(entity, closing), entity.aliases)


# ---- phrases (both languages) ------------------------------------------------------

_Q = {
    "what_open": {"ru": "Что открыть? Например: {names}.", "uk": "Що відкрити? Наприклад: {names}."},
    "what_close": {"ru": "Что закрыть? Например: {names}.", "uk": "Що закрити? Наприклад: {names}."},
    "which": {"ru": "Уточни, что именно: {names}?", "uk": "Уточни, що саме: {names}?"},
    "did_you_mean": {"ru": "Ты имел в виду {names}?", "uk": "Ти мав на увазі {names}?"},
    "not_found": {"ru": "Не нашёл такое. Доступно: {names}.", "uk": "Не знайшов такого. Доступно: {names}."},
    "percent": {"ru": "На сколько процентов поставить громкость?", "uk": "На скільки відсотків поставити гучність?"},
    "timer": {"ru": "На сколько минут поставить таймер?", "uk": "На скільки хвилин поставити таймер?"},
    "confirm_entity": {"ru": "Запустить {names}? Скажи «да» или повтори команду.", "uk": "Запустити {names}? Скажи «так» або повтори команду."},
    "which_game": {"ru": "Какую игру или режим запустить: {names}?", "uk": "Яку гру чи режим запустити: {names}?"},
}


def _clarify(key: str, options: list[Option], lang: str, kind: str = "choice") -> ParseResult:
    names = ", ".join(opt.label for opt in options[:_MAX_CLARIFY_OPTIONS])
    return ParseResult(
        clarification=Clarification(kind, _Q[key][lang].format(names=names), options[:_MAX_CLARIFY_OPTIONS]),
        language=lang,
    )


def _result(action_id: str, lang: str, **params: Any) -> ParseResult:
    return ParseResult(intent=Intent(action_id, params), language=lang)


# ---- main parser -------------------------------------------------------------------


def parse(text: str, catalog: Catalog, language: str | None = None) -> ParseResult:
    lang = language if language in ("ru", "uk") else guess_language(text)
    t = _strip_fillers(normalize(text))
    if not t:
        return ParseResult(unknown=True, language=lang)

    # Power first: "выключи звук" must not shut the PC down, so the mute check comes next.
    if _POWER_CANCEL.search(t):
        return _result("power_cancel", lang)
    if _RESTART.search(t):
        return _result("power_restart", lang)
    if _UNMUTE.search(t):
        return _result("unmute", lang)
    if _MUTE.search(t):
        return _result("mute", lang)
    if _SHUTDOWN.search(t):
        return _result("power_shutdown", lang)

    # Media
    if _PLAY_FROM_PAUSE.search(t):
        return _result("media_play", lang)
    if _MEDIA_PAUSE.search(t):
        return _result("media_pause", lang)
    if _MEDIA_PREV.search(t):
        return _result("media_previous", lang)
    if _MEDIA_NEXT.search(t):
        return _result("media_next", lang)
    if _MEDIA_PLAY.search(t) and not _entity_hit(t, catalog):
        return _result("media_play", lang)

    # Desktop helpers, time and timers
    if _TIMER_CANCEL.search(t):
        return _result("cancel_timers", lang)
    if _TIMER.search(t):
        minutes = _timer_minutes(t)
        if minutes is None:
            return ParseResult(clarification=Clarification("timer", _Q["timer"][lang]), language=lang)
        return _result("set_timer", lang, minutes=minutes)
    if _TIME_Q.search(t):
        return _result("current_time", lang)
    if _LOCK.search(t):
        return _result("lock_pc", lang)
    if _DESKTOP.search(t):
        return _result("show_desktop", lang)
    if _SCREENSHOT.search(t):
        return _result("screenshot", lang)

    # Volume: relative first ("тише", "прибавь на 10"), then absolute ("громкость 30")
    relative = _relative_volume(t)
    if relative is not None:
        return _result("volume_change", lang, delta=relative)
    if _VOLUME_WORD.search(t):
        number = extract_number(t)
        if number is None:
            return ParseResult(clarification=Clarification("percent", _Q["percent"][lang]), language=lang)
        return _result("set_volume", lang, percent=number)

    # Web search: "найди в ютубе котиков"
    if _SEARCH.search(t):
        searched = _parse_search(text, t, catalog, lang)
        if searched is not None:
            return searched

    # Information
    if _GAMING_STATUS.search(t):
        return _result("gaming_status", lang)
    if _RUNNING_APPS.search(t):
        return _result("running_apps", lang)
    if _LIST_APPS.search(t):
        return _result("list_apps", lang)
    if _LIST_SCENARIOS.search(t):
        return _result("list_scenarios", lang)
    if _PROCESSES.search(t):
        return _result("top_processes", lang)
    if _PING.search(t):
        return _result("ping", lang)
    if _STATUS.search(t):
        return _result("system_status", lang)

    return _parse_entity_command(t, catalog, lang)


def _entity_hit(text: str, catalog: Catalog) -> bool:
    exact, _ = _match_entities(text, _entities(catalog))
    return bool(exact)


def _parse_entity_command(t: str, catalog: Catalog, lang: str) -> ParseResult:
    closing = bool(_CLOSE.search(t)) and not _OPEN.search(t)
    opening = bool(_OPEN.search(t))
    entities = _entities(catalog)
    if not (opening or closing):
        # The verb may be misheard ("и спиткрой Steam"): a clearly heard name still gets a yes/no question.
        exact, _ = _match_entities(t, entities)
        if len(exact) == 1:
            return _clarify("confirm_entity", [_option(exact[0])], lang)
        return ParseResult(unknown=True, language=lang)
    if closing:
        entities = [e for e in entities if e.kind == "app"]
    exact, fuzzy = _match_entities(t, entities)

    if len(exact) == 1:
        return ParseResult(intent=_intent_for(exact[0], closing), language=lang)
    if len(exact) > 1:
        return _clarify("which", [_option(e, closing) for e in exact], lang)

    if fuzzy:
        best = fuzzy[0]
        close_ones = [e for score, e in fuzzy if score >= best[0] - 0.05]
        if best[0] >= 0.86 and len(close_ones) == 1:
            return ParseResult(intent=_intent_for(best[1], closing), language=lang)
        return _clarify("did_you_mean", [_option(e, closing) for e in close_ones[:3]], lang)

    if not closing and _GENERIC_GAME.search(t):
        options = [_option(e) for e in entities if e.kind == "game"]
        options += [_option(e) for e in entities if e.kind == "scenario" and e.key == "gaming"]
        if options:
            return _clarify("which_game", options, lang)

    pool = [e for e in entities if e.kind == "app"]
    if not closing:
        pool += [e for e in entities if e.kind == "scenario"]
    return _clarify("what_close" if closing else "what_open", [_option(e, closing) for e in pool], lang)


def _relative_volume(t: str) -> int | None:
    """'тише' -> -10, 'громче на 20' -> +20, 'убавь звук' -> -10. None when not a relative request."""
    sign = 0
    if _LOUDER.search(t):
        sign = 1
    elif _QUIETER.search(t):
        sign = -1
    elif _VOLUME_WORD.search(t):
        if _LOUDER_VERB.search(t):
            sign = 1
        elif _QUIETER_VERB.search(t):
            sign = -1
    if sign == 0:
        return None
    amount = extract_number(t)
    amount = 10 if amount is None else max(1, min(100, amount))
    return sign * amount


def _timer_minutes(t: str) -> int | None:
    if _HALF_HOUR.search(t):
        return 30
    number = extract_number(t)
    if number is None:
        return 60 if _HOUR.search(t) else None
    return number * 60 if _HOUR.search(t) else number


def _parse_search(text: str, t: str, catalog: Catalog, lang: str) -> ParseResult | None:
    """'найди в ютубе котиков' -> search_site(site_id, query). The query is only plain text."""
    sites = [e for e in _entities(catalog) if e.kind == "site"]
    exact, fuzzy = _match_entities(t, sites)
    site = exact[0] if len(exact) == 1 else (fuzzy[0][1] if fuzzy and fuzzy[0][0] >= 0.86 else None)
    if site is None:
        site = next((e for e in sites if e.key == _DEFAULT_SEARCH_SITE), None)
    if site is None:
        return None

    drop_prefixes = {alias[:4] for alias in site.aliases if " " not in alias and len(alias) >= 4}
    drop_words = {part for alias in site.aliases for part in alias.split()}
    words = []
    for word in text.split():
        n = normalize(word)
        if not n or (n in _SEARCH_STOP and not words) or n in _FILLERS or n in drop_words or n[:4] in drop_prefixes:
            continue
        if _SEARCH_NOISE.fullmatch(n):
            continue
        words.append(word.strip(" ,.!?;:"))
    query = " ".join(w for w in words if w)

    base = Intent("search_site", {"site_id": site.key})
    if not query:
        question = f"Что найти: {site.label}?" if lang == "ru" else f"Що знайти: {site.label}?"
        return ParseResult(
            clarification=Clarification("query", question, [Option(site.label, base, site.aliases)]), language=lang
        )
    return ParseResult(intent=Intent("search_site", {**base.params, "query": query}), language=lang)


# ---- follow-up answers to a clarification ------------------------------------------

_YES = {normalize(w) for w in ("да", "так", "ага", "угу", "ок", "окей", "давай", "yes", "ok", "верно", "точно", "звичайно", "да открой", "так відкрий")}

_ORDINALS = {
    normalize(w): i
    for i, words in enumerate(
        (["первый", "перший", "один", "1"], ["второй", "другий", "два", "2"], ["третий", "третій", "три", "3"]), start=0
    )
    for w in words
}


def resolve_clarification(text: str, pending: Clarification, language: str | None = None) -> ParseResult | None:
    """Interpret a short reply to a clarification question. None means: not an answer."""
    lang = language if language in ("ru", "uk") else guess_language(text)
    t = _strip_fillers(normalize(text))
    if not t:
        return None

    if pending.kind == "percent":
        number = extract_number(t)
        return _result("set_volume", lang, percent=number) if number is not None else None

    if pending.kind == "timer":
        minutes = _timer_minutes(t) or extract_number(t)
        return _result("set_timer", lang, minutes=minutes) if minutes else None

    if pending.kind == "query":
        base = pending.options[0].intent
        return ParseResult(intent=Intent(base.action_id, {**base.params, "query": text.strip()}), language=lang)

    tokens = t.split()
    if len(pending.options) == 1 and t in _YES:
        return ParseResult(intent=pending.options[0].intent, language=lang)
    exact = [opt for opt in pending.options if any(_find_exact(tokens, alias) for alias in opt.aliases)]
    if len(exact) == 1:
        return ParseResult(intent=exact[0].intent, language=lang)
    if not exact:
        scored = [
            (max((_fuzzy_score(tokens, alias) for alias in opt.aliases), default=0.0), opt) for opt in pending.options
        ]
        scored = [item for item in scored if item[0] >= 0.86]
        if len(scored) == 1:
            return ParseResult(intent=scored[0][1].intent, language=lang)
    if len(tokens) == 1 and tokens[0] in _ORDINALS and _ORDINALS[tokens[0]] < len(pending.options):
        return ParseResult(intent=pending.options[_ORDINALS[tokens[0]]].intent, language=lang)
    return None


def vocabulary_prompt(catalog: Catalog) -> str:
    """Names for the speech recogniser's initial prompt: helps it spell Steam, Discord, OBS..."""
    names: list[str] = []
    for group in (catalog.apps, catalog.scenarios, catalog.games, catalog.sites, catalog.folders):
        for label, aliases in group.values():
            names.append(label)
            names.extend(aliases[:2])
    return "Открой, відкрий, запусти, громкость, гучність, пауза: " + ", ".join(dict.fromkeys(names))
