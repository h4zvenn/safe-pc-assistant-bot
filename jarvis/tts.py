"""Local text-to-speech through Windows SAPI5. Nothing is saved or sent anywhere."""

from __future__ import annotations

import logging

from utils.com import com_apartment

log = logging.getLogger("jarvis.tts")

_LCID = {"ru": 0x419, "uk": 0x422}
_SVSF_IS_NOT_XML = 16


class SapiTts:
    def __init__(self) -> None:
        self._voices: dict[str, int] | None = None

    def _scan(self) -> dict[str, int]:
        """Map language -> index of the first installed SAPI voice for it."""
        if self._voices is not None:
            return self._voices
        found: dict[str, int] = {}
        try:
            import comtypes.client

            with com_apartment():
                voice = comtypes.client.CreateObject("SAPI.SpVoice")
                voices = voice.GetVoices()
                for index in range(voices.Count):
                    attr = voices.Item(index).GetAttribute("Language")  # e.g. "419" or "409;9"
                    codes = {int(part, 16) for part in str(attr).split(";") if part}
                    for lang, lcid in _LCID.items():
                        if lcid in codes:
                            found.setdefault(lang, index)
        except Exception as exc:
            log.warning("SAPI is not available: %s", exc)
        self._voices = found
        return found

    def languages(self) -> set[str]:
        return set(self._scan())

    def can_speak(self, lang: str) -> bool:
        return lang in self._scan()

    def speak(self, text: str, lang: str) -> bool:
        """Speak and wait until finished. False when no voice for that language is installed."""
        index = self._scan().get(lang)
        if index is None or not text.strip():
            return False
        try:
            import comtypes.client

            with com_apartment():
                voice = comtypes.client.CreateObject("SAPI.SpVoice")
                voice.Voice = voice.GetVoices().Item(index)
                voice.Speak(text, _SVSF_IS_NOT_XML)
            return True
        except Exception as exc:
            log.warning("TTS failed: %s", exc)
            return False
