from __future__ import annotations

import pytest

from jarvis.intent import extract_number, guess_language, normalize, parse, resolve_clarification


def result(service, text):
    return parse(text, service.catalog())


def action_of(service, text):
    parsed = result(service, text)
    assert parsed.intent is not None, f"{text!r}: {parsed}"
    return parsed.intent.action_id, parsed.intent.params


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Открой Steam", ("open_app", {"app_id": "steam"})),
        ("открой стим", ("open_app", {"app_id": "steam"})),
        ("Відкрий Discord", ("open_app", {"app_id": "discord"})),
        ("відкрий дискорд", ("open_app", {"app_id": "discord"})),
        ("запусти дискорд, пожалуйста", ("open_app", {"app_id": "discord"})),
        ("Джарвис, открой Steam", ("open_app", {"app_id": "steam"})),
        ("закрой дискорд", ("close_app", {"app_id": "discord"})),
        ("Громкость 30 процентов", ("set_volume", {"percent": 30})),
        ("гучність п'ятдесят відсотків", ("set_volume", {"percent": 50})),
        ("громкость тридцать процентов", ("set_volume", {"percent": 30})),
        ("поставь громкость на сто", ("set_volume", {"percent": 100})),
        ("Громкость 150 процентов", ("set_volume", {"percent": 150})),  # refused later by the action layer
        ("Поставь музыку на паузу", ("media_pause", {})),
        ("постав музику на паузу", ("media_pause", {})),
        ("пауза", ("media_pause", {})),
        ("продолжи музыку", ("media_play", {})),
        ("следующий трек", ("media_next", {})),
        ("предыдущий трек", ("media_previous", {})),
        ("Покажи состояние ПК", ("system_status", {})),
        ("покажи стан комп'ютера", ("system_status", {})),
        ("Запусти игровой режим", ("run_scenario", {"scenario_id": "gaming"})),
        ("запусти ігровий режим", ("run_scenario", {"scenario_id": "gaming"})),
        ("запусти рабочий режим", ("run_scenario", {"scenario_id": "work"})),
        ("запусти cs2", ("launch_game", {"game_id": "cs2"})),
        ("выключи звук", ("mute", {})),
        ("вимкни звук", ("mute", {})),
        ("включи звук", ("unmute", {})),
        ("выключи компьютер", ("power_shutdown", {})),
        ("вимкни комп'ютер", ("power_shutdown", {})),
        ("перезагрузи пк", ("power_restart", {})),
        ("отмени выключение", ("power_cancel", {})),
        ("пинг", ("ping", {})),
        ("игровой статус", ("gaming_status", {})),
    ],
)
def test_phrases(service, text, expected):
    assert action_of(service, text) == expected


def test_mute_is_not_mistaken_for_shutdown(service):
    assert action_of(service, "выключи звук")[0] == "mute"
    assert action_of(service, "выключи компьютер")[0] == "power_shutdown"


def test_ambiguous_game_request_asks_for_clarification(service):
    parsed = result(service, "запусти игру")
    assert parsed.intent is None and parsed.clarification is not None
    labels = [opt.label for opt in parsed.clarification.options]
    assert "CS2" in labels and "Gaming" in labels


def test_missing_object_asks_what_to_open(service):
    parsed = result(service, "открой")
    assert parsed.clarification is not None and "Steam" in parsed.clarification.question


def test_unknown_app_name_asks_instead_of_guessing(service):
    parsed = result(service, "открой сотейник")
    assert parsed.intent is None and parsed.clarification is not None


def test_misspelled_name_asks_for_confirmation_of_the_guess(service):
    parsed = result(service, "открой дискорт")
    assert parsed.clarification is not None
    assert [o.intent.params["app_id"] for o in parsed.clarification.options] == ["discord"]


def test_volume_without_number_asks_for_percent(service):
    parsed = result(service, "поставь громкость")
    assert parsed.clarification is not None and parsed.clarification.kind == "percent"
    follow = resolve_clarification("тридцать", parsed.clarification)
    assert follow.intent.action_id == "set_volume" and follow.intent.params == {"percent": 30}


def test_clarification_answers(service):
    parsed = result(service, "запусти игру")
    assert resolve_clarification("CS2", parsed.clarification).intent.action_id == "launch_game"
    assert resolve_clarification("первый", parsed.clarification).intent is not None
    assert resolve_clarification("что-то совсем другое", parsed.clarification) is None


def test_name_without_verb_gets_a_yes_no_question(service):
    parsed = result(service, "и спиткрой Steam")
    assert parsed.clarification is not None
    assert resolve_clarification("да", parsed.clarification).intent.params == {"app_id": "steam"}
    assert resolve_clarification("нет", parsed.clarification) is None


@pytest.mark.parametrize(
    "text",
    [
        "выполни powershell Get-Process",
        "запусти cmd /c del C:\\*",
        "открой C:/Windows/System32/cmd.exe",
        "скачай и запусти virus.exe",
        "расскажи анекдот",
        "",
        "   ",
    ],
)
def test_commands_outside_the_registry_are_not_understood(service, text):
    parsed = result(service, text)
    assert parsed.intent is None or parsed.intent.action_id in service.specs
    if parsed.intent is not None:
        # whatever was recognised must be a registered action with no command-line parameters
        assert set(parsed.intent.params) <= {"app_id", "scenario_id", "game_id", "percent", "limit"}


def test_injection_text_cannot_produce_free_form_parameters(service):
    parsed = result(service, "открой steam; Start-Process calc.exe -ArgumentList '/c'")
    assert parsed.intent.action_id == "open_app" and parsed.intent.params == {"app_id": "steam"}


@pytest.mark.parametrize(
    "text,number",
    [("30", 30), ("30%", 30), ("тридцать", 30), ("сто", 100), ("сто двадцать", 120), ("двадцать пять", 25), ("п'ятдесят", 50), ("на половину", 50), ("привет", None)],
)
def test_numbers(text, number):
    assert extract_number(text) == number


def test_language_guess():
    assert guess_language("Відкрий Discord") == "uk"
    assert guess_language("Открой Steam") == "ru"
    assert normalize("Відкрий, Дискорд!") == "видкрий дискорд"
