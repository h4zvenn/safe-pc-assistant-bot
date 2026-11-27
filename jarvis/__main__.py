"""JARVIS entry point.

  python -m jarvis                      local text mode (REPL)
  python -m jarvis --once "Открой Steam"
  python -m jarvis --voice              push-to-talk microphone mode
  python -m jarvis --wav sample.wav     recognise a WAV file, then act on it
  python -m jarvis --check              diagnostics (config, mic, speech model, TTS, Ollama)
"""

from __future__ import annotations

import argparse
import logging
import platform
import sys
import threading
import time
from pathlib import Path

from actions import ActionService, Channel
from config import AppConfig
from jarvis.intent import vocabulary_prompt
from jarvis.session import JarvisSession, Reply
from jarvis.settings import JarvisSettings
from services.config_validation import require_valid_config
from services.logging_service import setup_logging
from utils.yaml_loader import ConfigError

EXIT_WORDS = {"exit", "quit", "q", "выход", "вихід", "пока", "вийти"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m jarvis", description="Local JARVIS assistant (text and voice).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", metavar="TEXT", help="run one text command and exit")
    mode.add_argument("--voice", action="store_true", help="push-to-talk microphone mode")
    mode.add_argument("--listen", action="store_true", help="hands-free: listen all the time, react to 'Джарвис, ...'")
    mode.add_argument("--wav", nargs="+", metavar="FILE", help="recognise WAV/audio file(s) and run the command")
    mode.add_argument("--check", action="store_true", help="print diagnostics and exit")
    mode.add_argument("--list-mics", action="store_true", help="list input devices and exit")
    mode.add_argument("--mic-test", action="store_true", help="open the microphone for 1 s, report level, store nothing")
    p.add_argument("--dry-run", action="store_true", help="validate and describe actions without touching Windows")
    p.add_argument("--wake-word", action=argparse.BooleanOptionalAction, default=None, help="with --listen: require the wake word (default: yes)")
    p.add_argument("--tts", action=argparse.BooleanOptionalAction, default=None, help="spoken replies (default: on in --voice)")
    p.add_argument("--ollama", action=argparse.BooleanOptionalAction, default=None, help="free-form phrases via local Ollama")
    p.add_argument("--ptt-key", help="push-to-talk key, e.g. F9, scrolllock, ctrl+space, mouse4")
    p.add_argument("--stt-model", help="faster-whisper model: tiny, base, small, medium ...")
    p.add_argument("--device", choices=["cpu", "cuda", "auto"], help="speech recognition device")
    p.add_argument("--lang", choices=["ru", "uk"], help="force the language of text/replies")
    return p


def apply_overrides(settings: JarvisSettings, args: argparse.Namespace) -> None:
    if args.ptt_key:
        settings.ptt_key = args.ptt_key
    if args.stt_model:
        settings.stt_model = args.stt_model
    if args.device:
        settings.stt_device = args.device
        if args.device == "cuda":
            settings.stt_compute_type = "float16"
    if args.ollama is not None:
        settings.ollama_enabled = args.ollama
    if args.wake_word is not None:
        settings.listen_wake_word = args.wake_word
    if args.tts is not None:
        settings.tts_enabled = args.tts
    elif args.voice:
        settings.tts_enabled = settings.tts_enabled  # voice mode keeps the configured default


# ---- output ------------------------------------------------------------------------


class Console:
    def __init__(self, tts, speak: bool) -> None:
        self.tts = tts
        self.speak = speak
        self._warned: set[str] = set()
        self._speech_lock = threading.Lock()  # a timer can fire while a reply is being spoken

    def announce(self, text: str) -> None:
        """Called from a timer thread: print, beep and say it out loud."""
        print(f"\nJARVIS: {text}", flush=True)
        try:
            import winsound

            for _ in range(3):
                winsound.Beep(1000, 150)
        except Exception:  # no sound device: the printed line is enough
            pass
        if self.speak and self.tts is not None and self.tts.can_speak("ru"):
            with self._speech_lock:
                self.tts.speak(text, "ru")

    def show(self, reply: Reply) -> None:
        source = f" [{reply.parse_source}]" if reply.parse_source in ("llm", "chat") else ""
        print(f"JARVIS{source}: {reply.text}")
        if reply.detail:
            print("\n".join(f"    {line}" for line in reply.detail.splitlines()))
        if reply.confirm_code:
            print(f"    >>> Подтверждение только с клавиатуры: введи код {reply.confirm_code} (Enter). Голосом нельзя.")
        if self.speak and self.tts is not None:
            if self.tts.can_speak(reply.language):
                with self._speech_lock:
                    self.tts.speak(reply.text, reply.language)
            elif reply.language not in self._warned:
                self._warned.add(reply.language)
                print(f"    (нет установленного голоса для языка '{reply.language}': отвечаю только текстом)")


def read_line_with_timeout(timeout: float) -> str | None:
    """Read a line typed on the physical keyboard; None on timeout."""
    import msvcrt

    while msvcrt.kbhit():  # drop anything typed earlier
        msvcrt.getwch()
    buffer: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not msvcrt.kbhit():
            time.sleep(0.03)
            continue
        char = msvcrt.getwch()
        if char in ("\r", "\n"):
            print()
            return "".join(buffer)
        if char == "\x03":
            raise KeyboardInterrupt
        if char == "\b":
            if buffer:
                buffer.pop()
                print("\b \b", end="", flush=True)
        elif char.isprintable():
            buffer.append(char)
            print(char, end="", flush=True)
    print()
    return None


# ---- modes -------------------------------------------------------------------------


def run_text(session: JarvisSession, console: Console, lang: str | None) -> None:
    print("JARVIS (текстовый режим). Пиши команду, например «Открой Steam». Выход: exit.")
    while True:
        try:
            text = input("вы> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text.lower() in EXIT_WORDS:
            return
        console.show(session.handle(text, Channel.LOCAL_TEXT, lang))


def _beep(enabled: bool, freq: int, ms: int) -> None:
    if enabled:
        import winsound

        winsound.Beep(freq, ms)


def run_voice(session: JarvisSession, console: Console, settings: JarvisSettings, transcriber, config: AppConfig) -> None:
    from jarvis.audio import Recorder
    from jarvis.ptt import PushToTalk
    from jarvis.stt import SAMPLE_RATE

    ptt = PushToTalk(settings.ptt_key)
    recorder = Recorder(settings.input_device, settings.max_record_seconds)

    print(f"Загружаю модель распознавания «{settings.stt_model}»...")
    load_seconds = transcriber.load()
    print(f"Готово за {load_seconds:.1f} с ({transcriber.device}, {transcriber.compute_type}). Прогрев...")
    warm_seconds = transcriber.warmup()
    print(f"Прогрев {warm_seconds:.1f} с, устройство: {transcriber.device}.")
    print(f"Удерживай {settings.ptt_key.upper()} и говори; отпусти, чтобы отправить. Ctrl+C - выход.")
    print("Запись идёт только пока клавиша зажата. Аудио не сохраняется.")

    while True:
        try:
            ptt.wait_for_press()
            _beep(settings.beeps, 1000, 60)
            recorder.start()
            while ptt.is_down() and not recorder.full:
                print(f"\r[REC ●] {recorder.seconds:4.1f} c  ", end="", flush=True)
                time.sleep(0.05)
            audio = recorder.stop()
            print("\r[REC ■] стоп         ")
            _beep(settings.beeps, 600, 60)
            if audio.size < 0.4 * SAMPLE_RATE:
                print("    слишком короткая запись, пропускаю")
                continue

            transcript = transcriber.transcribe(audio)
            del audio  # recordings are never kept
            if not transcript.text:
                print(f"    речь не распознана ({transcript.seconds:.1f} с)")
                continue
            print(f"Вы ({transcript.language}, {transcript.seconds:.1f} с): {transcript.text}")
            reply = session.handle(transcript.text, Channel.LOCAL_VOICE, transcript.language)
            console.show(reply)

            if reply.confirm_code:
                _beep(settings.beeps, 800, 150)
                typed = read_line_with_timeout(config.local_confirmation_ttl_seconds)
                if typed is None:
                    print("    время подтверждения истекло, действие не выполнено")
                else:
                    console.show(session.handle(typed, Channel.LOCAL_TEXT, transcript.language))
        except KeyboardInterrupt:
            print("\nJARVIS остановлен.")
            return


def run_listen(
    session: JarvisSession,
    console: Console,
    settings: JarvisSettings,
    transcriber,
    config: AppConfig,
    source=None,
    ptt=None,
    out=print,
) -> None:
    """Hands-free mode: phrases are cut out of the microphone stream automatically.

    `source` and `ptt` can be replaced in tests; a source signals the end with EOFError.
    """
    import numpy as np

    from jarvis.listen import FrameSource, UtteranceSegmenter, WakeGate, looks_like_hallucination
    from jarvis.ptt import PushToTalk
    from jarvis.stt import SAMPLE_RATE

    own_source = source is None
    source = source or FrameSource(settings.input_device)
    ptt = ptt or PushToTalk(settings.ptt_key)
    gate = WakeGate(settings.listen_followup_seconds, settings.listen_wake_word)
    direct = WakeGate(require_wake_word=False)  # push-to-talk phrases need no wake word
    segmenter = UtteranceSegmenter(settings.listen_threshold)

    out(f"Загружаю модель распознавания «{settings.stt_model}»...")
    transcriber.load()
    transcriber.warmup()
    out(f"Модель готова ({transcriber.device}). Микрофон открыт, слушаю постоянно. Ctrl+C - выход.")
    if settings.listen_wake_word:
        out(f"Говори «Джарвис, <команда>». После ответа пару секунд можно без обращения. Или удерживай {settings.ptt_key.upper()}.")
    else:
        out("Обращение отключено: JARVIS реагирует на любую услышанную речь.")
    out("Аудио не сохраняется и не покидает компьютер. Распознанный текст не пишется в логи.")

    def process(audio, is_direct: bool) -> None:
        transcript = transcriber.transcribe(audio)
        text = transcript.text
        if not text or looks_like_hallucination(text):
            return
        accepted, command, wake_only = (direct if is_direct else gate).filter(text)
        if not accepted:
            out("    · услышал речь без обращения «Джарвис», пропускаю")
            return
        _beep(settings.beeps, 900, 40)
        if wake_only:
            console.show(Reply("Слушаю." if transcript.language == "ru" else "Слухаю.", language=transcript.language))
            gate.open_followup()
            return
        out(f"Вы ({transcript.language}, {transcript.seconds:.1f} с): {command}")
        reply = session.handle(command, Channel.LOCAL_VOICE, transcript.language)
        console.show(reply)
        if reply.confirm_code:
            _beep(settings.beeps, 800, 150)
            typed = read_line_with_timeout(config.local_confirmation_ttl_seconds)
            if typed is None:
                out("    время подтверждения истекло, действие не выполнено")
            else:
                console.show(session.handle(typed, Channel.LOCAL_TEXT, transcript.language))
        gate.open_followup()

    def finish() -> None:
        source.drain()  # audio heard while thinking or speaking (including our own voice) is dropped
        segmenter.reset()

    if own_source:
        source.start()
    held: list = []
    in_voice = False
    try:
        while True:
            try:
                frame = source.read(0.1)
            except EOFError:
                return
            if frame is None:
                continue

            if ptt.is_down():
                held.append(frame)
                print(f"\r[REC ●] {sum(len(f) for f in held) / SAMPLE_RATE:4.1f} c  ", end="", flush=True)
                continue
            if held:
                audio, held = np.concatenate(held), []
                print("\r[REC ■] стоп         ")
                if len(audio) > 0.4 * SAMPLE_RATE:
                    process(audio, True)
                    finish()
                continue

            audio = segmenter.feed(frame)
            if segmenter.speaking != in_voice:
                in_voice = segmenter.speaking
                print("\r[● голос]       " if in_voice else "\r" + " " * 20 + "\r", end="", flush=True)
            if audio is not None:
                process(audio, False)
                finish()
                in_voice = False
    except KeyboardInterrupt:
        out("\nJARVIS остановлен.")
    finally:
        if own_source:
            source.stop()


def run_wav(session: JarvisSession, console: Console, transcriber, paths: list[str]) -> int:
    code = 0
    load_seconds = transcriber.load()
    print(f"Модель {transcriber.model_name} ({transcriber.device}, {transcriber.compute_type}) загружена за {load_seconds:.1f} с")
    for path in paths:
        if not Path(path).is_file():
            print(f"Файл не найден: {path}")
            code = 1
            continue
        transcript = transcriber.transcribe(path)
        rtf = transcript.seconds / transcript.audio_seconds if transcript.audio_seconds else 0
        print(
            f"{Path(path).name}: «{transcript.text}» [{transcript.language}] "
            f"аудио {transcript.audio_seconds:.1f} с, распознавание {transcript.seconds:.2f} с (RTF {rtf:.2f})"
        )
        if transcript.text:
            console.show(session.handle(transcript.text, Channel.LOCAL_VOICE, transcript.language))
    return code


def run_check(config: AppConfig, settings: JarvisSettings, service: ActionService) -> int:
    from jarvis.llm import OllamaParser
    from jarvis.stt import cuda_available
    from jarvis.tts import SapiTts

    print(f"Python {platform.python_version()} ({sys.executable})")
    print(f"data: {config.data_dir}")
    issues = []
    try:
        issues = require_valid_config(config.data_dir)
        print("data/*.yaml: OK")
    except ConfigError as exc:
        print(exc)
        return 1
    for issue in issues:
        print(f"  {issue}")

    catalog = service.catalog()
    print(f"приложений: {len(catalog.apps)}, сценариев: {len(catalog.scenarios)}, игр: {len(catalog.games)}")

    try:
        from jarvis.audio import list_input_devices

        mics = list_input_devices()
        print("микрофоны: " + ("; ".join(f"{i}: {n}{' (по умолчанию)' if d else ''}" for i, n, d in mics) or "не найдены"))
    except Exception as exc:
        print(f"микрофоны: ошибка {exc}")

    try:
        import faster_whisper  # noqa: F401

        print(f"распознавание: faster-whisper установлен, модель {settings.stt_model}, устройство {settings.stt_device}, CUDA доступна: {cuda_available()}")
    except ImportError:
        print("распознавание: faster-whisper не установлен (pip install -r requirements-voice.txt)")

    tts = SapiTts()
    print(f"TTS (SAPI) языки: {sorted(tts.languages()) or 'нет голосов ru/uk'}")

    parser = OllamaParser(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_seconds)
    print(f"Ollama ({settings.ollama_url}): {'доступна' if parser.is_available() else 'не запущена'}, использование: {'вкл' if settings.ollama_enabled else 'выкл'}")
    print(f"push-to-talk: {settings.ptt_key}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    try:
        config = AppConfig.from_env(require_telegram=False)
        settings = JarvisSettings.from_env()
        apply_overrides(settings, args)
        setup_logging(config.log_dir, console=False)
        for issue in require_valid_config(config.data_dir):
            logging.getLogger("jarvis").warning("%s", issue)
    except (RuntimeError, ConfigError) as exc:
        print(f"Ошибка настройки: {exc}")
        return 2

    service = ActionService(config, dry_run=args.dry_run)

    if args.check:
        return run_check(config, settings, service)
    if args.list_mics:
        from jarvis.audio import list_input_devices

        for index, name, default in list_input_devices():
            print(f"{index}: {name}{'  (по умолчанию)' if default else ''}")
        return 0
    if args.mic_test:
        from jarvis.audio import mic_level_test

        ok, message = mic_level_test(settings.input_device)
        print(message)
        return 0 if ok else 1

    llm = None
    if settings.ollama_enabled:
        from jarvis.llm import OllamaParser

        llm = OllamaParser(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_seconds)
        if not llm.is_available():
            print("Ollama не запущена: разговор и свободные формулировки отключены, базовые команды работают.")
            llm = None

    session = JarvisSession(service, llm)
    if llm is not None:
        session.on_thinking = lambda: print("    (думаю...)", flush=True)
        import threading

        threading.Thread(target=llm.warmup, daemon=True).start()  # load the model in the background
    voice_like = args.voice or args.listen or bool(args.wav)
    speak = settings.tts_enabled if args.tts is None and voice_like else bool(args.tts)
    tts = None
    if speak:
        from jarvis.tts import SapiTts

        tts = SapiTts()
    console = Console(tts, speak)
    if not (args.once or args.wav):  # one-shot runs exit at once, so a timer would never ring
        service.notifier = console.announce  # timers speak up through the console
    if args.dry_run:
        print("[dry-run] изменяющие действия не выполняются")

    if args.once:
        reply = session.handle(args.once, Channel.LOCAL_TEXT, args.lang)
        console.show(reply)
        if reply.confirm_code:
            print("    (одиночная команда не подтверждается: запусти интерактивный режим `python -m jarvis`)")
        return 0 if not reply.result or reply.result.status in ("ok", "needs_confirmation") else 1

    if voice_like:
        from jarvis.stt import Transcriber

        transcriber = Transcriber(
            settings.stt_model,
            settings.stt_device,
            settings.stt_compute_type,
            settings.languages,
            prompt=vocabulary_prompt(service.catalog()),
        )
        if args.wav:
            return run_wav(session, console, transcriber, args.wav)
        if args.listen:
            run_listen(session, console, settings, transcriber, config)
        else:
            run_voice(session, console, settings, transcriber, config)
        return 0

    run_text(session, console, args.lang)
    return 0


if __name__ == "__main__":
    sys.exit(main())
