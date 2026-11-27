from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_ENV_REF = re.compile(r"%([^%]+)%")


def _fallback(name: str) -> str | None:
    """Defaults for Windows variables that can be missing (services, sandboxes, odd shells)."""
    home = os.environ.get("USERPROFILE") or str(Path.home())
    system_drive = os.environ.get("SystemDrive", "C:")
    defaults = {
        "USERPROFILE": home,
        "LOCALAPPDATA": str(Path(home) / "AppData" / "Local"),
        "APPDATA": str(Path(home) / "AppData" / "Roaming"),
        "PROGRAMFILES": f"{system_drive}/Program Files",
        "PROGRAMFILES(X86)": f"{system_drive}/Program Files (x86)",
        "PROGRAMDATA": f"{system_drive}/ProgramData",
        "SYSTEMDRIVE": system_drive,
        "SYSTEMROOT": f"{system_drive}/Windows",
        "WINDIR": f"{system_drive}/Windows",
    }
    return defaults.get(name.upper())


def expand_path(raw: str | os.PathLike[str]) -> Path:
    """Expand %VAR% references and ~ without ever running anything."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.environ.get(name) or _fallback(name)
        return value if value else match.group(0)

    text = _ENV_REF.sub(replace, str(raw))
    return Path(os.path.expanduser(text))


def system_drive_root() -> str:
    return os.environ.get("SystemDrive", "C:") + "\\"
