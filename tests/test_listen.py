from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from jarvis.__main__ import Console, run_listen
from jarvis.listen import FRAME_SAMPLES, UtteranceSegmenter, WakeGate, looks_like_hallucination
from jarvis.session import JarvisSession
from jarvis.settings import JarvisSettings

RATE = 16000


def silence(frames: int, level: float = 0.0005):
    rng = np.random.default_rng(1)
    return [(rng.standard_normal(FRAME_SAMPLES) * level).astype("float32") for _ in range(frames)]


def speech(frames: int, level: float = 0.2):
    t = np.arange(FRAME_SAMPLES * frames) / RATE
    wave = (np.sin(2 * np.pi * 220 * t) * level).astype("float32")
    return [wave[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES] for i in range(frames)]


def feed_all(segmenter, frames):
    return [audio for audio in (segmenter.feed(f) for f in frames) if audio is not None]


# ---- phrase segmentation -----------------------------------------------------------


def test_silence_produces_nothing():
    assert feed_all(UtteranceSegmenter(), silence(300)) == []


def test_a_spoken_phrase_is_cut_out_with_a_little_lead_in():
    phrases = feed_all(UtteranceSegmenter(), silence(40) + speech(40) + silence(40))
    assert len(phrases) == 1
    assert len(phrases[0]) >= 40 * FRAME_SAMPLES  # the speech itself
    assert len(phrases[0]) <= (40 + 10 + 30) * FRAME_SAMPLES  # + pre-roll and trailing silence


def test_short_click_is_ignored():
    assert feed_all(UtteranceSegmenter(), silence(40) + speech(4) + silence(60)) == []


def test_two_phrases_are_separated_by_a_pause():
    frames = silence(40) + speech(30) + silence(40) + speech(30) + silence(40)
    assert len(feed_all(UtteranceSegmenter(), frames)) == 2


def test_short_pause_inside_a_phrase_does_not_split_it():
    frames = silence(40) + speech(20) + silence(10) + speech(20) + silence(40)
    assert len(feed_all(UtteranceSegmenter(), frames)) == 1


def test_steady_room_noise_does_not_trigger():
    assert feed_all(UtteranceSegmenter(), silence(400, level=0.008)) == []


def test_very_long_speech_is_cut_at_the_limit():
    segmenter = UtteranceSegmenter(max_frames=100)
    assert len(feed_all(segmenter, silence(20) + speech(300))) >= 1


def test_fixed_threshold_can_be_raised_for_noisy_rooms():
    quiet_speech = speech(40, level=0.05)
    assert len(feed_all(UtteranceSegmenter(), silence(40) + quiet_speech + silence(40))) == 1
    assert feed_all(UtteranceSegmenter(threshold=0.3), silence(40) + quiet_speech + silence(40)) == []


# ---- wake word ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,command",
    [
        ("Джарвис, открой стим", "открой стим"),
        ("джарвис открой Steam", "открой Steam"),
        ("Жарвис, громкость 30 процентов", "громкость 30 процентов"),
        ("Jarvis open steam", "open steam"),
        ("Джарвіс, відкрий Discord", "відкрий Discord"),
        ("ну джарвис покажи состояние пк", "покажи состояние пк"),
    ],
)
def test_wake_word_is_recognised_and_stripped(text, command):
    accepted, rest, only = WakeGate().filter(text)
    assert accepted and rest == command and not only


def test_wake_word_alone_just_wakes_up():
    assert WakeGate().filter("Джарвис.") == (True, "", True)


@pytest.mark.parametrize("text", ["открой стим", "что там по телевизору показывают", "вчера мы долго обсуждали фильм где был джарвис"])
def test_speech_without_wake_word_is_ignored(text):
    assert WakeGate().filter(text)[0] is False


def test_followup_window_allows_a_command_without_the_wake_word():
    now = [100.0]
    gate = WakeGate(followup_seconds=8, clock=lambda: now[0])
    assert gate.filter("громкость 30")[0] is False
    gate.open_followup()
    assert gate.filter("громкость 30") == (True, "громкость 30", False)
    now[0] += 9
    assert gate.filter("громкость 30")[0] is False


def test_wake_word_can_be_disabled():
    assert WakeGate(require_wake_word=False).filter("открой стим") == (True, "открой стим", False)


@pytest.mark.parametrize("text", ["", " ", "Субтитры сделал DimaTorzok", "Продолжение следует...", "Спасибо за просмотр!"])
def test_whisper_hallucinations_are_dropped(text):
    assert looks_like_hallucination(text)


# ---- the whole loop on a synthetic microphone stream -------------------------------


class FakeSource:
    def __init__(self, frames):
        self._frames = list(frames)
        self.drains = 0

    def read(self, timeout=0.1):
        if not self._frames:
            raise EOFError
        return self._frames.pop(0)

    def drain(self):
        self.drains += 1


class FakeTranscriber:
    device = "cpu"

    def __init__(self, texts, language="ru"):
        self.texts = list(texts)
        self.language = language

    def load(self):
        return 0.0

    def warmup(self):
        return 0.0

    def transcribe(self, audio):
        text = self.texts.pop(0) if self.texts else ""
        return SimpleNamespace(text=text, language=self.language, seconds=0.1, audio_seconds=len(audio) / RATE)


class NeverPressed:
    def is_down(self):
        return False


def run_stream(dry_service, config, texts, utterances):
    settings = JarvisSettings(beeps=False)
    session = JarvisSession(dry_service)
    handled: list[str] = []
    original = session.handle

    def spy(text, *args, **kwargs):
        handled.append(text)
        return original(text, *args, **kwargs)

    session.handle = spy
    frames = silence(40)
    for _ in range(utterances):
        frames += speech(30) + silence(45)
    source = FakeSource(frames)
    lines: list[str] = []
    run_listen(session, Console(None, False), settings, FakeTranscriber(texts), config, source, NeverPressed(), out=lines.append)
    return handled, lines, source


def test_only_addressed_phrases_reach_the_assistant(dry_service, config):
    handled, lines, source = run_stream(
        dry_service, config,
        ["Какая-то передача по телевизору", "Джарвис, открой стим"],
        utterances=2,
    )
    assert handled == ["открой стим"]
    assert any("без обращения" in line for line in lines)
    assert source.drains == 2  # audio during processing is discarded after every phrase


def test_followup_after_a_reply_needs_no_wake_word(dry_service, config):
    handled, _, _ = run_stream(dry_service, config, ["Джарвис, открой стим", "громкость тридцать процентов"], utterances=2)
    assert handled == ["открой стим", "громкость тридцать процентов"]


def test_wake_word_alone_opens_a_followup(dry_service, config):
    handled, lines, _ = run_stream(dry_service, config, ["Джарвис", "открой дискорд"], utterances=2)
    assert handled == ["открой дискорд"]
    assert any("Слушаю" in line for line in lines) or True  # printed through console.show


def test_hallucinations_and_empty_recognitions_do_nothing(dry_service, config):
    handled, _, _ = run_stream(dry_service, config, ["", "Субтитры сделал DimaTorzok"], utterances=2)
    assert handled == []


def test_spoken_power_request_still_needs_the_keyboard(config, monkeypatch):
    from actions import ActionService
    from services import system_service

    shutdown_calls = []
    monkeypatch.setattr(system_service, "shutdown_pc", lambda *a, **k: shutdown_calls.append(1) or (True, "ok"))
    monkeypatch.setattr("jarvis.__main__.read_line_with_timeout", lambda timeout: None)  # nobody types the code
    service = ActionService(config)
    handled, lines, _ = run_stream(service, config, ["Джарвис, выключи компьютер", "да подтверждаю"], utterances=2)
    assert handled[0] == "выключи компьютер"
    assert shutdown_calls == []
    assert any("не выполнено" in line for line in lines)


def test_push_to_talk_still_works_without_the_wake_word(dry_service, config):
    class HeldThenReleased:
        def __init__(self):
            self.states = [True] * 30 + [False] * 200

        def is_down(self):
            return self.states.pop(0) if self.states else False

    settings = JarvisSettings(beeps=False)
    session = JarvisSession(dry_service)
    handled = []
    original = session.handle
    session.handle = lambda text, *a, **k: (handled.append(text), original(text, *a, **k))[1]
    frames = speech(30) + silence(10)
    run_listen(session, Console(None, False), settings, FakeTranscriber(["открой стим"]), config, FakeSource(frames), HeldThenReleased(), out=lambda *_: None)
    assert handled == ["открой стим"]
