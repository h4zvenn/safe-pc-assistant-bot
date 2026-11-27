from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass

import psutil

from utils.com import com_apartment as _com
from utils.paths import system_drive_root

_HOST_PATTERN = re.compile(r"^(?!-)[A-Za-z0-9.\-]{1,253}$")


@dataclass(slots=True)
class HealthStatus:
    cpu_percent: float
    memory_percent: float
    disk_percent: float


@dataclass(slots=True)
class ProcessEntry:
    pid: int
    name: str
    cpu_percent: float
    memory_mb: float


def _get_volume_controller():
    # Imported lazily so a broken audio stack only affects volume commands.
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def get_health_status() -> HealthStatus:
    cpu = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage(system_drive_root()).percent
    return HealthStatus(
        cpu_percent=cpu,
        memory_percent=memory,
        disk_percent=disk,
    )


def get_system_snapshot() -> dict[str, float]:
    health = get_health_status()
    memory = psutil.virtual_memory()
    return {
        "cpu_percent": health.cpu_percent,
        "memory_percent": health.memory_percent,
        "disk_percent": health.disk_percent,
        "memory_used_gb": (memory.total - memory.available) / 1024**3,
        "memory_total_gb": memory.total / 1024**3,
        "uptime_hours": (time.time() - psutil.boot_time()) / 3600,
    }


def get_heavy_processes(limit: int = 5) -> list[ProcessEntry]:
    processes = list(psutil.process_iter(["pid", "name"]))

    for proc in processes:
        try:
            proc.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(0.3)

    cores = psutil.cpu_count() or 1
    result: list[ProcessEntry] = []
    for proc in processes:
        if proc.pid == 0:  # "System Idle Process" is the inverse of load, not a program
            continue
        try:
            # psutil reports per-core percentages (up to cores * 100); normalise to the whole CPU.
            cpu = proc.cpu_percent(interval=None) / cores
            memory_mb = proc.memory_info().rss / 1024 / 1024
            result.append(
                ProcessEntry(
                    pid=proc.pid,
                    name=proc.info.get("name") or "unknown",
                    cpu_percent=cpu,
                    memory_mb=memory_mb,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    result.sort(key=lambda x: (x.cpu_percent, x.memory_mb), reverse=True)
    return result[:limit]


def is_valid_host(host: str) -> bool:
    return bool(_HOST_PATTERN.match(host))


def ping_host(host: str = "8.8.8.8") -> tuple[bool, str]:
    if not is_valid_host(host):
        return False, "Ping error: invalid host"
    try:
        # Output is in the console OEM code page; only the exit code is needed.
        completed = subprocess.run(
            ["ping", "-n", "1", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            shell=False,
        )
        if completed.returncode == 0:
            return True, f"Ping OK: {host}"
        return False, f"Ping failed: {host}"
    except Exception as exc:
        return False, f"Ping error: {exc}"


def change_volume(delta: int) -> tuple[bool, str, int]:
    """Louder/quieter by delta points, clamped to 0..100. Returns (ok, message, new percent)."""
    if isinstance(delta, bool) or not isinstance(delta, int) or not -100 <= delta <= 100:
        return False, "Volume error: delta must be an integer from -100 to 100.", -1
    try:
        current, _ = get_volume()
        target = max(0, min(100, current + delta))
        ok, message = set_volume(target)
        return ok, message, target
    except Exception as exc:
        return False, f"Volume error: {exc}", -1


def get_volume() -> tuple[int, bool]:
    """Return (percent, muted) of the default output device."""
    with _com():
        volume = _get_volume_controller()
        return round(volume.GetMasterVolumeLevelScalar() * 100), bool(volume.GetMute())


def set_volume(level: int) -> tuple[bool, str]:
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
        return False, "Volume error: level must be an integer from 0 to 100."
    try:
        with _com():
            volume = _get_volume_controller()
            volume.SetMute(0, None)
            volume.SetMasterVolumeLevelScalar(level / 100, None)
        return True, f"Volume set to {level}%."
    except Exception as exc:
        return False, f"Volume error: {exc}"


def mute_volume() -> tuple[bool, str]:
    try:
        with _com():
            _get_volume_controller().SetMute(1, None)
        return True, "Volume muted."
    except Exception as exc:
        return False, f"Mute error: {exc}"


def unmute_volume() -> tuple[bool, str]:
    try:
        with _com():
            _get_volume_controller().SetMute(0, None)
        return True, "Volume unmuted."
    except Exception as exc:
        return False, f"Unmute error: {exc}"


def top_processes(limit: int = 5) -> list[dict[str, float | int | str]]:
    items = get_heavy_processes(limit=limit)
    return [
        {
            "pid": item.pid,
            "name": item.name,
            "cpu_percent": item.cpu_percent,
            "memory_mb": item.memory_mb,
        }
        for item in items
    ]


def _run_shutdown(args: list[str], ok_message: str) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            ["shutdown", *args],
            capture_output=True,
            timeout=10,
            shell=False,
        )
    except Exception as exc:
        return False, f"Power error: {exc}"
    if completed.returncode == 0:
        return True, ok_message
    output = (completed.stderr or completed.stdout).decode("oem", errors="replace").strip()
    return False, f"Power error: {output or completed.returncode}"


def shutdown_pc(delay_seconds: int = 30) -> tuple[bool, str]:
    """Schedule a shutdown with a delay so it can still be cancelled."""
    return _run_shutdown(
        ["/s", "/t", str(int(delay_seconds))],
        f"Выключение через {int(delay_seconds)} с. Отмена: «отмени выключение» или shutdown /a.",
    )


def restart_pc(delay_seconds: int = 30) -> tuple[bool, str]:
    return _run_shutdown(
        ["/r", "/t", str(int(delay_seconds))],
        f"Перезагрузка через {int(delay_seconds)} с. Отмена: «отмени выключение» или shutdown /a.",
    )


def cancel_shutdown() -> tuple[bool, str]:
    ok, message = _run_shutdown(["/a"], "Запланированное выключение или перезагрузка отменены.")
    if not ok:
        return False, "Нет запланированного выключения, которое можно отменить."
    return ok, message
