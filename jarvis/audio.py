"""Microphone capture. Audio lives only in memory and is dropped after recognition."""

from __future__ import annotations

import threading
from typing import Any

from jarvis.stt import SAMPLE_RATE


def list_input_devices() -> list[tuple[int, str, bool]]:
    """(index, name, is_default) for every input-capable device."""
    import sounddevice as sd

    default = sd.default.device[0]
    return [
        (index, f"{info['name']} [{sd.query_hostapis(info['hostapi'])['name']}]", index == default)
        for index, info in enumerate(sd.query_devices())
        if info["max_input_channels"] > 0
    ]


def _device(raw: str) -> int | str | None:
    if not raw:
        return None
    return int(raw) if raw.isdigit() else raw


class Recorder:
    """Records 16 kHz mono float32 between start() and stop()."""

    def __init__(self, device: str = "", max_seconds: int = 15) -> None:
        self._device = _device(device)
        self._max_frames = max_seconds * SAMPLE_RATE
        self._chunks: list[Any] = []
        self._frames = 0
        self._stream: Any = None
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        with self._lock:
            if self._frames < self._max_frames:
                self._chunks.append(indata[:, 0].copy())
                self._frames += frames

    def start(self) -> None:
        import sounddevice as sd

        self._chunks, self._frames = [], 0
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()

    @property
    def seconds(self) -> float:
        return self._frames / SAMPLE_RATE

    @property
    def full(self) -> bool:
        return self._frames >= self._max_frames

    def stop(self):
        import numpy as np

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")


def mic_level_test(device: str = "", seconds: float = 1.0) -> tuple[bool, str]:
    """Open the microphone for a moment and report the peak level. Nothing is stored."""
    import numpy as np

    try:
        recorder = Recorder(device, max_seconds=max(2, int(seconds) + 1))
        recorder.start()
        import time

        time.sleep(seconds)
        audio = recorder.stop()
    except Exception as exc:
        return False, f"Микрофон недоступен: {exc}"
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    return audio.size > 0, f"Микрофон открывается, записано {audio.size / SAMPLE_RATE:.1f} с, пик {peak:.3f}."
