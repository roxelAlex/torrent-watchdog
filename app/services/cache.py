"""Кэш с TTL для ответов внешних сервисов.

qBittorrent опрашивается при каждом рендере главной, настроек и /health.
Недоступный клиент стоит целого таймаута соединения, поэтому короткий кэш
превращает «страница висит минуту» в «страница висит один раз за TTL».
"""

import threading
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class TTLCache:
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def get_or_set(self, key: str, producer: Callable[[], T]) -> T:
        entry = self._fresh(key)
        if entry is not None:
            return entry[1]
        # Один поток идёт в сеть, остальные ждут его результат, а не собственный таймаут.
        with self._lock_for(key):
            entry = self._fresh(key)
            if entry is not None:
                return entry[1]
            value = producer()
            with self._guard:
                self._entries[key] = (time.monotonic(), value)
            return value

    def invalidate(self, key: str | None = None) -> None:
        with self._guard:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)

    def _fresh(self, key: str) -> tuple[float, Any] | None:
        with self._guard:
            entry = self._entries.get(key)
        if entry and time.monotonic() - entry[0] < self.ttl_seconds:
            return entry
        return None

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())
