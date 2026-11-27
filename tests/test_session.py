from __future__ import annotations

import io
import logging

import pytest

from actions import ActionService, Channel
from jarvis.llm import OllamaParser, proposal_to_intent, proposal_to_understanding
from jarvis.ptt import parse_key
from jarvis.session import JarvisSession
from services import apps_service, system_service
from services.logging_service import RedactingFormatter
from tests.conftest import Recorder


class FakeLLM:
    def __init__(self, proposal):
        self.proposal = proposal
        self.calls = 0

    def understand(self, text, catalog, specs, history=None, language="ru"):
        self.calls += 1
        self.last_history = list(history or [])
        return proposal_to_understanding(self.proposal, specs)


# ---- the voice -> confirmation boundary --------------------------------------------


def test_voice_can_request_but_not_confirm_shutdown(service, monkeypatch):
    shutdown = Recorder((True, "scheduled"))
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    session = JarvisSession(service)

    ask = session.handle("выключи компьютер", Channel.LOCAL_VOICE)
    assert ask.confirm_code and shutdown.calls == []

    # spoken yes, spoken code, spoken "confirm": none of it may run the action
    for spoken in ("да", "подтверждаю", ask.confirm_code, f"подтвердить {ask.confirm_code}"):
        session.handle(spoken, Channel.LOCAL_VOICE)
    assert shutdown.calls == []

    done = session.handle(ask.confirm_code, Channel.LOCAL_TEXT)  # typed on the keyboard
    assert done.result.status == "ok" and len(shutdown.calls) == 1


def test_typed_confirmation_with_wrong_code_does_nothing(service, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    session = JarvisSession(service)
    ask = session.handle("перезагрузи пк", Channel.LOCAL_TEXT)
    wrong = "0000" if ask.confirm_code != "0000" else "1111"
    assert session.handle(wrong, Channel.LOCAL_TEXT).result.status == "error"
    assert shutdown.calls == []


def test_typed_cancel_drops_pending_confirmation(service, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    session = JarvisSession(service)
    ask = session.handle("выключи пк", Channel.LOCAL_TEXT)
    assert session.handle("отмена", Channel.LOCAL_TEXT).result.status == "ok"
    assert session.handle(ask.confirm_code, Channel.LOCAL_TEXT).result.status == "error"
    assert shutdown.calls == []


def test_close_request_by_voice_needs_keyboard_confirmation(service, monkeypatch):
    closer = Recorder((True, "closed"))
    monkeypatch.setattr(apps_service, "close_allowed_app", closer)
    session = JarvisSession(service)
    ask = session.handle("закрой дискорд", Channel.LOCAL_VOICE)
    assert ask.confirm_code and closer.calls == []


# ---- the full phrase -> action -> reply chain --------------------------------------


def test_voice_phrase_to_windows_action_to_reply(service, monkeypatch):
    setter = Recorder((True, "Volume set to 30%."))
    monkeypatch.setattr(system_service, "set_volume", setter)
    reply = JarvisSession(service).handle("Громкость 30 процентов", Channel.LOCAL_VOICE)
    assert setter.calls == [((30,), {})]
    assert reply.text == "Громкость 30 процентов." and reply.result.status == "ok"


def test_ukrainian_phrase_gets_ukrainian_reply(service, monkeypatch):
    monkeypatch.setattr(apps_service.subprocess, "Popen", lambda *a, **k: None)
    reply = JarvisSession(service).handle("Відкрий Discord", Channel.LOCAL_VOICE)
    assert reply.language == "uk" and reply.text == "Discord відкрито."


def test_volume_over_100_is_refused_end_to_end(service, monkeypatch):
    setter = Recorder()
    monkeypatch.setattr(system_service, "set_volume", setter)
    reply = JarvisSession(service).handle("громкость 150 процентов", Channel.LOCAL_VOICE)
    assert reply.result.status == "error" and setter.calls == []


def test_missing_app_reply_is_clear(service, forbid_processes):
    reply = JarvisSession(service).handle("открой спотифай", Channel.LOCAL_VOICE)
    assert reply.result.status == "error" and "Spotify" in reply.detail


def test_clarification_follow_up_runs_the_chosen_action(dry_service):
    session = JarvisSession(dry_service)
    first = session.handle("запусти игру", Channel.LOCAL_VOICE)
    assert first.result is None and "CS2" in first.text
    second = session.handle("CS2", Channel.LOCAL_VOICE)
    assert second.result.action_id == "launch_game"


def test_clarification_expires(dry_service):
    now = [0.0]
    session = JarvisSession(dry_service, clock=lambda: now[0])
    session.handle("запусти игру", Channel.LOCAL_VOICE)
    now[0] += 120
    reply = session.handle("CS2", Channel.LOCAL_VOICE)
    assert reply.result is None  # "CS2" alone is only a yes/no question now


def test_unknown_phrase_replies_without_acting(service, forbid_processes):
    reply = JarvisSession(service).handle("расскажи анекдот", Channel.LOCAL_VOICE)
    assert reply.result is None and reply.text == "Не понял команду."


# ---- optional LLM ------------------------------------------------------------------


def test_llm_is_only_asked_when_rules_fail(service):
    llm = FakeLLM({"action_id": "system_status"})
    session = JarvisSession(service, llm=llm)
    session.handle("Открой Steam", Channel.LOCAL_TEXT)
    assert llm.calls == 0
    session.handle("хочу поболтать с друзьями", Channel.LOCAL_TEXT)
    assert llm.calls == 1


def test_llm_free_form_intent_goes_through_the_same_validation(dry_service):
    llm = FakeLLM({"action_id": "open_app", "app_id": "steam"})
    reply = JarvisSession(dry_service, llm=llm).handle("хочу поиграть", Channel.LOCAL_VOICE)
    assert reply.result.action_id == "open_app" and reply.parse_source == "llm"


def test_llm_cannot_pick_unregistered_action_or_unknown_app(service, forbid_processes):
    assert proposal_to_intent({"action_id": "run_powershell", "command": "calc"}, service.specs) is None
    assert proposal_to_intent("open_app", service.specs) is None
    assert proposal_to_intent({"action_id": "none"}, service.specs) is None
    llm = FakeLLM({"action_id": "open_app", "app_id": "../../cmd"})
    reply = JarvisSession(service, llm=llm).handle("сделай что-нибудь", Channel.LOCAL_VOICE)
    assert reply.result.status == "error"


def test_llm_extra_fields_are_dropped(service):
    intent = proposal_to_intent(
        {"action_id": "open_app", "app_id": "steam", "command": "calc.exe", "args": ["x"], "path": "C:/x.exe"}, service.specs
    )
    assert intent.params == {"app_id": "steam"}


def test_llm_cannot_skip_confirmation(service, monkeypatch):
    shutdown = Recorder()
    monkeypatch.setattr(system_service, "shutdown_pc", shutdown)
    llm = FakeLLM({"action_id": "power_shutdown"})
    reply = JarvisSession(service, llm=llm).handle("мне пора спать", Channel.LOCAL_VOICE)
    assert reply.confirm_code and shutdown.calls == []


def test_ollama_must_be_local():
    OllamaParser("http://127.0.0.1:11434", "m")
    OllamaParser("http://localhost:11434", "m")
    for url in ("http://example.com:11434", "http://192.168.1.5:11434", "https://api.openai.com"):
        with pytest.raises(ValueError):
            OllamaParser(url, "m")


def test_ollama_down_means_no_intent(service):
    parser = OllamaParser("http://127.0.0.1:9", "m", timeout_seconds=1)
    assert parser.parse("привет", service.catalog(), service.specs) is None


# ---- misc --------------------------------------------------------------------------


def test_ptt_key_names():
    assert parse_key("F9") == [0x78]
    assert parse_key("ctrl+space") == [0x11, 0x20]
    assert parse_key("a") == [0x41]
    assert parse_key("mouse4") == [0x05]
    with pytest.raises(ValueError):
        parse_key("hyperkey")


def test_telegram_token_is_redacted_from_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    log = logging.getLogger("redaction-test")
    log.handlers = [handler]
    log.propagate = False
    log.error("failed: https://api.telegram.org/bot1234567890:AAH_abcdefghijklmnopqrstuvwxyz012345/getMe")
    assert "AAH_abc" not in stream.getvalue() and "<redacted-token>" in stream.getvalue()


# ---- conversation ------------------------------------------------------------------


def test_non_command_gets_a_conversational_reply_without_any_action(service, forbid_processes):
    llm = FakeLLM({"action_id": "none", "reply": "Всё отлично, спасибо что спросил!"})
    reply = JarvisSession(service, llm=llm).handle("как дела", Channel.LOCAL_VOICE)
    assert reply.text == "Всё отлично, спасибо что спросил!"
    assert reply.result is None and reply.parse_source == "chat"


def test_reply_text_never_executes_anything(service, forbid_processes):
    llm = FakeLLM({"action_id": "none", "reply": "Открываю Steam и выключаю компьютер: powershell calc"})
    reply = JarvisSession(service, llm=llm).handle("расскажи что-нибудь", Channel.LOCAL_VOICE)
    assert reply.result is None  # it is only text


def test_conversation_keeps_recent_history(service):
    llm = FakeLLM({"action_id": "none", "reply": "Конечно."})
    session = JarvisSession(service, llm=llm)
    session.handle("давай поболтаем", Channel.LOCAL_TEXT)
    session.handle("а ты умеешь шутить", Channel.LOCAL_TEXT)
    assert llm.last_history == [("давай поболтаем", "Конечно.")]


def test_history_is_bounded(service):
    llm = FakeLLM({"action_id": "none", "reply": "ок"})
    session = JarvisSession(service, llm=llm)
    for i in range(20):
        session.handle(f"болтовня {i}", Channel.LOCAL_TEXT)
    assert len(session._history) == 6


def test_basic_commands_do_not_wait_for_the_llm(dry_service):
    llm = FakeLLM({"action_id": "none", "reply": "не должно вызываться"})
    reply = JarvisSession(dry_service, llm=llm).handle("Открой Steam", Channel.LOCAL_TEXT)
    assert llm.calls == 0 and reply.result.action_id == "open_app"


def test_empty_or_garbage_llm_answer_falls_back_to_not_understood(service):
    for proposal in ({"action_id": "none"}, {"action_id": "none", "reply": "   "}, "text", None):
        reply = JarvisSession(service, llm=FakeLLM(proposal)).handle("бла бла", Channel.LOCAL_TEXT)
        assert reply.text == "Не понял команду."


def test_thinking_hook_is_called_only_for_llm_requests(dry_service):
    session = JarvisSession(dry_service, llm=FakeLLM({"action_id": "none", "reply": "ок"}))
    hits = []
    session.on_thinking = lambda: hits.append(1)
    session.handle("Открой Steam", Channel.LOCAL_TEXT)
    session.handle("как жизнь", Channel.LOCAL_TEXT)
    assert hits == [1]
