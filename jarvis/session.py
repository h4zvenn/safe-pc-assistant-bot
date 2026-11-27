"""One conversation with JARVIS: text in, registered action out, short reply back.

The same session serves the local text mode and the microphone. The channel decides what
a request may do: spoken text can ask for a sensitive action, but only the local keyboard
(LOCAL_TEXT) can confirm it.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

from actions import ActionResult, ActionService, Actor, Channel
from jarvis import replies
from jarvis.intent import (
    Clarification,
    Intent,
    ParseResult,
    guess_language,
    normalize,
    parse,
    resolve_clarification,
)
from jarvis.llm import OllamaParser

log = logging.getLogger("jarvis.session")

CLARIFICATION_TTL_SECONDS = 30
MAX_HISTORY_TURNS = 6
_CONFIRM_CODE = re.compile(r"^(?:confirm|подтверд\w*|підтверд\w*|да|так|yes|ok)?\s*(\d{4})$")
_CANCEL = {normalize(w) for w in ("cancel", "отмена", "отмени", "скасувати", "скасуй", "нет", "ні")}


@dataclass(slots=True)
class Reply:
    text: str  # short reply, in the user's language (this is what gets spoken)
    detail: str = ""  # full text from the action, if different
    language: str = "ru"
    result: ActionResult | None = None
    intent: Intent | None = None
    parse_source: str = ""  # "rules" | "llm" | ""
    confirm_code: str = ""  # set when the local keyboard must confirm


class JarvisSession:
    def __init__(
        self,
        service: ActionService,
        llm: OllamaParser | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service = service
        self.llm = llm
        self._clock = clock
        self._pending: tuple[Clarification, float] | None = None
        self._confirm_token: str | None = None
        self._history: list[tuple[str, str]] = []  # last (user, assistant) turns, kept in memory only
        self.on_thinking: Callable[[], None] | None = None  # called before a slow LLM request

    # ---- public -------------------------------------------------------------------

    def handle(self, text: str, channel: Channel = Channel.LOCAL_TEXT, language: str | None = None) -> Reply:
        lang = language if language in ("ru", "uk") else guess_language(text)

        if channel is Channel.LOCAL_TEXT:
            confirmation = self._handle_confirmation_input(text, lang)
            if confirmation is not None:
                return confirmation

        parsed = self._parse(text, lang)
        lang = parsed.language

        if parsed.clarification is not None:
            self._pending = (parsed.clarification, self._clock() + CLARIFICATION_TTL_SECONDS)
            return Reply(parsed.clarification.question, language=lang)

        if parsed.chat_reply is not None:
            self._remember(text, parsed.chat_reply)
            return Reply(parsed.chat_reply, language=lang, parse_source="chat")

        if parsed.intent is None:
            return Reply(replies.UNKNOWN[lang], language=lang)

        reply = self._execute(parsed.intent, channel, lang)
        self._remember(text, reply.text)
        return reply

    def _remember(self, user_text: str, assistant_text: str) -> None:
        self._history.append((user_text, assistant_text))
        del self._history[:-MAX_HISTORY_TURNS]

    # ---- internals ----------------------------------------------------------------

    def _parse(self, text: str, lang: str) -> ParseResult:
        catalog = self.service.catalog()

        if self._pending is not None:
            clarification, expires = self._pending
            self._pending = None
            if self._clock() <= expires:
                resolved = resolve_clarification(text, clarification, lang)
                if resolved is not None:
                    return resolved

        parsed = parse(text, catalog, lang)
        if parsed.unknown and self.llm is not None:
            if self.on_thinking is not None:
                self.on_thinking()
            understood = self.llm.understand(text, catalog, self.service.specs, self._history, parsed.language)
            if understood.intent is not None:
                return ParseResult(intent=understood.intent, language=parsed.language)
            if understood.reply:
                return ParseResult(chat_reply=understood.reply, language=parsed.language)
        return parsed

    def _execute(self, intent: Intent, channel: Channel, lang: str) -> Reply:
        result = self.service.execute(intent.action_id, intent.params, Actor(channel))
        label = self._label(intent)

        if result.status == "needs_confirmation" and result.confirmation is not None:
            self._confirm_token = result.confirmation.token
            return Reply(
                replies.reply_for(result, lang, label),
                detail=result.message,
                language=lang,
                result=result,
                intent=intent,
                parse_source=intent.source,
                confirm_code=result.confirmation.token,
            )

        return self._reply_from(result, lang, label, intent)

    def _handle_confirmation_input(self, text: str, lang: str) -> Reply | None:
        """Typed confirmation codes. Only reachable from the local text channel."""
        cleaned = normalize(text)
        actor = Actor(Channel.LOCAL_TEXT)

        if self._confirm_token and cleaned in _CANCEL:
            result = self.service.cancel(self._confirm_token, actor)
            self._confirm_token = None
            return self._reply_from(result, lang, "", None)

        match = _CONFIRM_CODE.match(cleaned)
        if match is None:
            return None
        result = self.service.confirm(match.group(1), actor)
        self._confirm_token = None
        return self._reply_from(result, lang, "", None)

    def _reply_from(self, result: ActionResult, lang: str, label: str, intent: Intent | None) -> Reply:
        text = replies.reply_for(result, lang, label)
        detail = result.message if result.message and result.message != text else ""
        if result.code in ("missing_param", "invalid_params"):
            detail = ""  # "Не указан параметр app_id" means nothing to the person who spoke
        return Reply(
            text,
            detail=detail,
            language=lang,
            result=result,
            intent=intent,
            parse_source=intent.source if intent else "",
        )

    def _label(self, intent: Intent) -> str:
        catalog = self.service.catalog()
        for key, group in (
            ("app_id", catalog.apps),
            ("scenario_id", catalog.scenarios),
            ("game_id", catalog.games),
            ("site_id", catalog.sites),
            ("folder_id", catalog.folders),
        ):
            value = intent.params.get(key)
            if isinstance(value, str) and value.lower() in group:
                return group[value.lower()][0]
        return ""
