"""Small in-memory TTL cache with LRU eviction."""

from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Thread-safe in-memory cache with max size and TTL."""

    def __init__(self, max_size: int, ttl_seconds: int):
        self.max_size = max(1, max_size)
        self.ttl_seconds = max(1, ttl_seconds)
        self._data: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> T | None:
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._data.pop(key, None)
                return None
            # Mark as recently used
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: T) -> None:
        expires_at = time.time() + self.ttl_seconds
        with self._lock:
            if key in self._data:
                self._data.pop(key, None)
            self._data[key] = (expires_at, value)
            self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        now = time.time()
        # Prune expired items first
        expired_keys = [k for k, (exp, _) in self._data.items() if exp <= now]
        for k in expired_keys:
            self._data.pop(k, None)
        while len(self._data) > self.max_size:
            self._data.popitem(last=False)

