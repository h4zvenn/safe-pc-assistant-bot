"""Voice -> recognised phrase -> action -> reply, using a locally synthesised WAV.

Skipped automatically when the speech model is not downloaded or no Russian SAPI voice exists.
Run on purpose with:  python -m pytest -m voice -v
"""

from __future__ import annotations

import pytest

from actions import Channel
from jarvis.intent import vocabulary_prompt
from jarvis.session import JarvisSession
from jarvis.tts import SapiTts

pytestmark = pytest.mark.voice

MODEL = "small"


def _model_cached() -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model(MODEL, local_files_only=True)
        return True
    except Exception:
        return False


def synth_wav(path, text: str) -> None:
    import comtypes.client

    from utils.com import com_apartment

    with com_apartment():
        voice = comtypes.client.CreateObject("SAPI.SpVoice")
        voices = voice.GetVoices()
        for index in range(voices.Count):
            if "419" in str(voices.Item(index).GetAttribute("Language")).split(";"):
                voice.Voice = voices.Item(index)
        stream = comtypes.client.CreateObject("SAPI.SpFileStream")
        stream.Format.Type = 22  # 22 kHz, 16 bit, mono
        stream.Open(str(path), 3)
        voice.AudioOutputStream = stream
        voice.Speak(text, 16)
        stream.Close()


@pytest.fixture(scope="module")
def transcriber_factory():
    pytest.importorskip("faster_whisper")
    if not _model_cached():
        pytest.skip(f"faster-whisper model {MODEL!r} is not downloaded")
    if "ru" not in SapiTts().languages():
        pytest.skip("no Russian SAPI voice installed")
    from jarvis.stt import Transcriber

    return lambda prompt: Transcriber(MODEL, "cpu", "int8", ("ru", "uk"), prompt=prompt)


@pytest.mark.parametrize(
    "phrase,action_id,params",
    [
        ("Открой стим", "open_app", {"app_id": "steam"}),
        ("Громкость тридцать процентов", "set_volume", {"percent": 30}),
        ("Запусти игровой режим", "run_scenario", {"scenario_id": "gaming"}),
        ("Поставь музыку на паузу", "media_pause", {}),
    ],
)
def test_wav_to_action_chain(tmp_path, dry_service, transcriber_factory, phrase, action_id, params):
    wav = tmp_path / "phrase.wav"
    synth_wav(wav, phrase)

    transcriber = transcriber_factory(vocabulary_prompt(dry_service.catalog()))
    transcript = transcriber.transcribe(wav)
    assert transcript.text, "nothing was recognised"

    reply = JarvisSession(dry_service).handle(transcript.text, Channel.LOCAL_VOICE, transcript.language)

    assert reply.intent is not None, f"not understood: {transcript.text!r}"
    assert (reply.intent.action_id, reply.intent.params) == (action_id, params), transcript.text
    assert reply.result.status == "ok"  # dry-run: nothing on this machine was touched


def test_hands_free_stream_with_wake_word(tmp_path, dry_service, config, transcriber_factory):
    """Silence, an unaddressed phrase, then 'Джарвис, ...': only the addressed one is executed."""
    import numpy as np
    from faster_whisper import decode_audio

    from jarvis.__main__ import Console, run_listen
    from jarvis.listen import FRAME_SAMPLES
    from jarvis.settings import JarvisSettings

    clips = []
    for name, phrase in (("a", "Открой дискорд"), ("b", "Джарвис, открой стим")):
        wav = tmp_path / f"{name}.wav"
        synth_wav(wav, phrase)
        clips.append(decode_audio(str(wav), sampling_rate=16000))

    gap = np.zeros(16000 * 2, dtype="float32")
    stream = np.concatenate([gap, clips[0], gap, clips[1], gap]).astype("float32")
    frames = [stream[i : i + FRAME_SAMPLES] for i in range(0, len(stream) - FRAME_SAMPLES, FRAME_SAMPLES)]

    class Source:
        def read(self, timeout=0.1):
            if not frames:
                raise EOFError
            return frames.pop(0)

        def drain(self):
            pass

    class Never:
        def is_down(self):
            return False

    session = JarvisSession(dry_service)
    handled = []
    original = session.handle
    session.handle = lambda text, *a, **k: (handled.append(text), original(text, *a, **k))[1]
    transcriber = transcriber_factory(vocabulary_prompt(dry_service.catalog()))
    run_listen(session, Console(None, False), JarvisSettings(beeps=False), transcriber, config, Source(), Never(), out=lambda *_: None)

    assert len(handled) == 1, handled
    assert "стим" in handled[0].lower() or "steam" in handled[0].lower()
