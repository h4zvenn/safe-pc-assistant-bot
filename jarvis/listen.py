"""Hands-free listening: split the microphone stream into phrases and require a wake word.

Everything stays local and in memory. Phrases are recognised by the same Transcriber as the
push-to-talk mode. Speech without the wake word ("Джарвис, ...") is dropped unless it follows a
reply within the follow-up window, so a TV or a conversation in the room does not trigger anything.
"""

from __future__ import annotations

import difflib
import queue
import time
from collections import deque
from typing import Any, Callable

from jarvis.intent import normalize
from jarvis.stt import SAMPLE_RATE

FRAME_SAMPLES = 480  # 30 ms at 16 kHz

_WAKE_WORDS = sorted(
    {
        normalize(w)
        for w in (
            "джарвис", "джарвіс", "жарвис", "джервис", "джарвес", "чарвис", "харвис", "гарвис", "ярвис",
            "jarvis", "жарвіс",
        )
    }
)
# Whisper hallucinates these on noise and silence.
_HALLUCINATIONS = ("субтитр", "продолжение следует", "спасибо за просмотр", "дякую за перегляд", "подписывайтесь")
_STRIP = " ,.!?-—:;…"


# ---- phrase segmentation -----------------------------------------------------------


class UtteranceSegmenter:
    """Energy-based phrase detector with an adaptive noise floor.

    feed() takes one 30 ms frame and returns the finished phrase (float32 array) or None.
    Non-speech noise that slips through is dropped later by the recogniser's own VAD.
    """

    def __init__(
        self,
        threshold: float = 0.0,
        min_threshold: float = 0.015,
        start_frames: int = 3,
        end_silence_frames: int = 27,  # 0.8 s
        preroll_frames: int = 10,  # 0.3 s kept before the start so the first consonant survives
        max_frames: int = 500,  # 15 s
        min_speech_frames: int = 10,
    ) -> None:
        self.fixed_threshold = threshold
        self.min_threshold = min_threshold
        self.start_frames = start_frames
        self.end_silence_frames = end_silence_frames
        self.max_frames = max_frames
        self.min_speech_frames = min_speech_frames
        self._preroll: deque[Any] = deque(maxlen=preroll_frames)
        self.noise_floor = 0.005
        self.reset()

    def reset(self) -> None:
        self._preroll.clear()
        self._frames: list[Any] = []
        self._speaking = False
        self._loud = 0
        self._silent = 0
        self._speech_frames = 0

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def threshold(self) -> float:
        if self.fixed_threshold > 0:
            return self.fixed_threshold
        return max(self.min_threshold, self.noise_floor * 3.5)

    @staticmethod
    def rms(frame: Any) -> float:
        import numpy as np

        return float(np.sqrt(np.mean(np.square(frame, dtype="float32")))) if len(frame) else 0.0

    def feed(self, frame: Any):
        import numpy as np

        level = self.rms(frame)
        loud = level > self.threshold

        if not self._speaking:
            self._preroll.append(frame)
            if not loud:
                self.noise_floor = 0.98 * self.noise_floor + 0.02 * level  # adapt only to background
                self._loud = 0
                return None
            self._loud += 1
            if self._loud < self.start_frames:
                return None
            self._speaking = True
            self._frames = list(self._preroll)
            self._speech_frames = self._loud
            self._silent = 0
            return None

        self._frames.append(frame)
        if loud:
            self._speech_frames += 1
            self._silent = 0
        else:
            self._silent += 1

        if self._silent >= self.end_silence_frames or len(self._frames) >= self.max_frames:
            enough = self._speech_frames >= self.min_speech_frames
            audio = np.concatenate(self._frames) if enough else None
            self.reset()
            return audio
        return None


# ---- wake word ---------------------------------------------------------------------


def _is_wake_word(word: str) -> bool:
    if not word:
        return False
    if word in _WAKE_WORDS:
        return True
    return len(word) >= 5 and any(difflib.SequenceMatcher(None, word, w).ratio() >= 0.8 for w in _WAKE_WORDS)


def looks_like_hallucination(text: str) -> bool:
    lowered = text.lower()
    return len(normalize(text)) < 2 or any(marker in lowered for marker in _HALLUCINATIONS)


class WakeGate:
    """Decides whether a recognised phrase is addressed to JARVIS."""

    def __init__(
        self,
        followup_seconds: float = 8.0,
        require_wake_word: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.followup_seconds = followup_seconds
        self.require_wake_word = require_wake_word
        self._clock = clock
        self._open_until = 0.0

    def open_followup(self) -> None:
        self._open_until = self._clock() + self.followup_seconds

    @property
    def followup_open(self) -> bool:
        return self._clock() < self._open_until

    def filter(self, text: str) -> tuple[bool, str, bool]:
        """Return (accepted, command_text, wake_word_only)."""
        words = text.split()
        for index, word in enumerate(words[:3]):
            if _is_wake_word(normalize(word)):
                rest = " ".join(words[index + 1 :]).strip(_STRIP)
                return True, rest, rest == ""
        if not self.require_wake_word or self.followup_open:
            return True, text.strip(), False
        return False, "", False


# ---- microphone frames -------------------------------------------------------------


class FrameSource:
    """Continuous microphone capture delivering 30 ms frames."""

    def __init__(self, device: str = "") -> None:
        self._device: int | str | None = (int(device) if device.isdigit() else device) if device else None
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=400)
        self._stream: Any = None

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            try:
                self._queue.get_nowait()  # drop the oldest frame rather than block the audio thread
            except queue.Empty:
                pass

    def start(self) -> None:
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()

    def read(self, timeout: float = 0.2):
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        """Throw away audio captured while JARVIS was busy or speaking (avoids hearing itself)."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
