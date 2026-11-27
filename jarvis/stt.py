"""Local speech recognition with faster-whisper (Russian / Ukrainian)."""

from __future__ import annotations

import importlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.stt")

SAMPLE_RATE = 16000
PAD_SECONDS = 0.3


@dataclass(slots=True)
class Transcript:
    text: str
    language: str
    audio_seconds: float
    seconds: float  # recognition latency (language detection + decoding)
    model: str
    device: str


def _add_cuda_dll_dirs() -> None:
    """Make the pip-installed CUDA libraries (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable."""
    for package in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            module = importlib.import_module(package)
        except ImportError:
            continue
        for base in getattr(module, "__path__", []):
            bin_dir = Path(base) / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def cuda_available() -> bool:
    """A CUDA device exists AND the CUDA 12 runtime libraries can be loaded."""
    _add_cuda_dll_dirs()
    try:
        import ctypes

        import ctranslate2

        if ctranslate2.get_cuda_device_count() == 0:
            return False
        ctypes.WinDLL("cublas64_12.dll")
        return True
    except Exception:
        return False


class Transcriber:
    def __init__(
        self,
        model: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        languages: tuple[str, ...] = ("ru", "uk"),
        prompt: str | None = None,
    ) -> None:
        self.model_name = model
        self.requested_device = device
        self.compute_type = compute_type
        self.languages = languages
        self.prompt = prompt
        self.device = device
        self._model: Any = None
        self._language = languages[0]

    def load(self) -> float:
        """Load the model (downloaded once into the Hugging Face cache). Returns load seconds."""
        if self._model is not None:
            return 0.0
        from faster_whisper import WhisperModel

        _add_cuda_dll_dirs()
        device = self.requested_device
        compute = self.compute_type
        if device == "auto":
            device = "cuda" if cuda_available() else "cpu"
        if device == "cuda" and compute == "int8":
            compute = "float16"

        started = time.perf_counter()
        try:
            self._model = WhisperModel(self.model_name, device=device, compute_type=compute)
        except Exception as exc:
            if device == "cpu":
                raise
            log.warning("GPU unavailable (%s); falling back to CPU", exc)
            device, compute = "cpu", "int8"
            self._model = WhisperModel(self.model_name, device=device, compute_type=compute)
        self.device = device
        self.compute_type = compute
        return time.perf_counter() - started

    def warmup(self) -> float:
        """Run one dummy inference so the first real phrase is not slow (GPU init takes ~14 s)."""
        import numpy as np

        started = time.perf_counter()
        try:
            self.transcribe(np.zeros(SAMPLE_RATE, dtype="float32"))
        except Exception as exc:  # warm-up is best effort
            log.warning("warm-up failed: %s", exc)
        return time.perf_counter() - started

    def _audio(self, source: Any) -> Any:
        if isinstance(source, (str, Path)):
            from faster_whisper import decode_audio

            return decode_audio(str(source), sampling_rate=SAMPLE_RATE)
        return source

    def _recognise(self, audio: Any) -> list[Any]:
        language = self.languages[0]
        if len(self.languages) > 1:
            # Whisper often mixes up ru/uk, so pick the better of the allowed languages only.
            _, _, probabilities = self._model.detect_language(audio)
            allowed = {code: prob for code, prob in probabilities if code in self.languages}
            if allowed:
                language = max(allowed, key=allowed.get)
        self._language = language

        segments, _ = self._model.transcribe(
            audio,
            language=language,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=self.prompt,
            temperature=0.0,
        )
        return list(segments)  # decoding is lazy: force it here so errors surface in this call

    def transcribe(self, source: Any) -> Transcript:
        """source: float32 mono 16 kHz array, or a path to an audio file."""
        self.load()
        audio = self._audio(source)
        started = time.perf_counter()
        audio_seconds = len(audio) / SAMPLE_RATE
        # Silence around the clip: without it Whisper tends to drop the first consonant
        # when speech starts right at the key press.
        import numpy as np

        pad = np.zeros(int(PAD_SECONDS * SAMPLE_RATE), dtype="float32")
        audio = np.concatenate([pad, audio.astype("float32", copy=False), pad])

        try:
            segments = self._recognise(audio)
        except RuntimeError as exc:
            if self.device != "cuda":
                raise
            # CUDA libraries are often missing even when a GPU is present: fall back to CPU.
            log.warning("GPU inference failed (%s); falling back to CPU", exc)
            self._model = None
            self.requested_device, self.compute_type = "cpu", "int8"
            self.load()
            segments = self._recognise(audio)
        language = self._language
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return Transcript(
            text=text,
            language=language,
            audio_seconds=audio_seconds,
            seconds=time.perf_counter() - started,
            model=self.model_name,
            device=self.device,
        )
