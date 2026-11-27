from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_FAILED_LOCAL_ATTEMPTS = 3


@dataclass(slots=True)
class PendingConfirmation:
    token: str
    user_id: int
    group: str  # "telegram" or "local": a confirmation never crosses groups
    action_id: str
    params: dict[str, Any]
    summary: str
    expires_at: float = 0.0
    created_by: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ConfirmationStore:
    """Short-lived, one-shot confirmations bound to a user and a channel group."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._items: dict[str, PendingConfirmation] = {}
        self._failed: dict[str, int] = {}
        self._clock = clock

    def _purge(self) -> None:
        now = self._clock()
        for token in [t for t, item in self._items.items() if item.expires_at < now]:
            del self._items[token]

    def _new_token(self, group: str) -> str:
        while True:
            if group == "local":
                token = f"{secrets.randbelow(10000):04d}"
            else:
                token = secrets.token_urlsafe(8)
            if token not in self._items:
                return token

    def create(
        self,
        *,
        user_id: int,
        group: str,
        action_id: str,
        params: dict[str, Any],
        summary: str,
        ttl_seconds: int,
        created_by: str = "",
    ) -> PendingConfirmation:
        self._purge()
        # One open request per user and group: a new one replaces the old.
        for token in [t for t, i in self._items.items() if i.user_id == user_id and i.group == group]:
            del self._items[token]
        item = PendingConfirmation(
            token=self._new_token(group),
            user_id=user_id,
            group=group,
            action_id=action_id,
            params=dict(params),
            summary=summary,
            expires_at=self._clock() + ttl_seconds,
            created_by=created_by,
        )
        self._items[item.token] = item
        return item

    def take(self, token: str, user_id: int, group: str) -> PendingConfirmation | None:
        """Consume a confirmation if it exists, is fresh and belongs to this user and group."""
        self._purge()
        item = self._items.get(token)
        if item is None or item.user_id != user_id or item.group != group:
            self._register_failure(group)
            return None
        del self._items[token]
        self._failed[group] = 0
        return item

    def cancel(self, token: str, user_id: int, group: str) -> bool:
        item = self._items.get(token)
        if item is None or item.user_id != user_id or item.group != group:
            return False
        del self._items[token]
        return True

    def _register_failure(self, group: str) -> None:
        # Local codes are only four digits; stop guessing after a few misses.
        if group != "local":
            return
        self._failed[group] = self._failed.get(group, 0) + 1
        if self._failed[group] >= MAX_FAILED_LOCAL_ATTEMPTS:
            for token in [t for t, i in self._items.items() if i.group == group]:
                del self._items[token]
            self._failed[group] = 0
