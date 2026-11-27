from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Literal

from security.confirmations import PendingConfirmation

Status = Literal["ok", "partial", "error", "denied", "needs_confirmation"]


class Channel(str, Enum):
    TELEGRAM = "telegram"
    LOCAL_TEXT = "local_text"
    LOCAL_VOICE = "local_voice"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who asks for an action. user_id is the Telegram id, or 0 for the local machine."""

    channel: Channel
    user_id: int = 0

    @property
    def group(self) -> str:
        return "telegram" if self.channel is Channel.TELEGRAM else "local"


class ActionError(Exception):
    """A request that fails validation. The message is safe to show to the user."""

    def __init__(self, message: str, code: str = "invalid_params") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(slots=True)
class ActionResult:
    status: Status
    action_id: str
    message: str
    title: str = ""
    code: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    confirmation: PendingConfirmation | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    kind: Literal[
        "app_id", "closable_app_id", "scenario_id", "game_id", "site_id", "folder_id", "percent", "limit", "query", "delta", "minutes"
    ]
    required: bool = True


@dataclass(frozen=True, slots=True)
class ActionSpec:
    id: str
    title: str
    description: str
    handler: Callable[[Any, dict[str, Any]], ActionResult]
    params: tuple[ParamSpec, ...] = ()
    sensitive: bool = False  # always needs a separate confirmation
    mutating: bool = True  # changes the machine state (skipped in dry-run mode)
