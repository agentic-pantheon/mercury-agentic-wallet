"""In-memory deduplication for Alchemy webhook retries (MVP TTL store)."""

from __future__ import annotations

import time


class AlchemyWebhookDedupeStore:
    """Remember recently seen dedupe keys to avoid double-processing webhook retries.

    Keys are typically ``(webhook_id, event_id, tx_hash, log_index)``. Entries expire
    after ``ttl_seconds`` (monotonic clock). This is process-local memory only.
    """

    def __init__(self, *, ttl_seconds: float = 3600.0, max_entries: int = 50_000) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._expires_at: dict[tuple[str, str, str, str], float] = {}

    def mark_if_new(self, key: tuple[str, str, str, str]) -> bool:
        """Return ``True`` for first delivery within the TTL."""

        now = time.monotonic()
        self._purge_expired(now)
        deadline = self._expires_at.get(key)
        if deadline is not None and deadline > now:
            return False
        self._expires_at[key] = now + self._ttl
        self._enforce_max(now)
        return True

    def _purge_expired(self, now: float) -> None:
        dead = [key for key, exp in self._expires_at.items() if exp <= now]
        for key in dead:
            del self._expires_at[key]

    def _enforce_max(self, now: float) -> None:
        if len(self._expires_at) <= self._max:
            return
        self._purge_expired(now)
        if len(self._expires_at) <= self._max:
            return
        sorted_items = sorted(self._expires_at.items(), key=lambda item: item[1])
        overflow = len(self._expires_at) - self._max
        for key, _ in sorted_items[:overflow]:
            del self._expires_at[key]
