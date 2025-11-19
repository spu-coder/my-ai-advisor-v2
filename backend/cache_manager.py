"""
Cache Manager
============
Centralized cache abstraction that prefers Redis and falls back to an
in-memory TTL cache. Used by security, RAG, and LLM layers to avoid
repeated heavy work.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional

try:  # pragma: no cover - optional dependency
    import redis  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    redis = None  # type: ignore


class _InMemoryTTLCache:
    """Lightweight thread-safe TTL cache as a Redis fallback."""

    def __init__(self):
        self._store: Dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at < time.time():
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expires_at = time.time() + ttl_seconds
        with self._lock:
            self._store[key] = (expires_at, value)


class CacheManager:
    """Simple cache facade with optional Redis backend."""

    def __init__(self):
        redis_url = os.getenv("REDIS_CACHE_URL") or os.getenv("RATE_LIMIT_REDIS_URL")
        self._redis_client = None
        if redis_url and redis:
            try:
                self._redis_client = redis.Redis.from_url(redis_url)
            except Exception:  # pragma: no cover - connection failure
                self._redis_client = None
        self._fallback_cache = _InMemoryTTLCache()

    def _serialize(self, value: Any) -> str:
        if isinstance(value, (str, bytes)):
            return value if isinstance(value, str) else value.decode("utf-8")
        return json.dumps(value, ensure_ascii=False)

    def _deserialize(self, cached: Optional[bytes | str]) -> Optional[Any]:
        if cached is None:
            return None
        if isinstance(cached, bytes):
            cached = cached.decode("utf-8")
        try:
            return json.loads(cached)
        except json.JSONDecodeError:
            return cached

    def get(self, key: str) -> Optional[Any]:
        if self._redis_client:
            try:
                cached = self._redis_client.get(key)
                if cached is not None:
                    return self._deserialize(cached)
            except Exception:
                pass  # fallback below
        cached = self._fallback_cache.get(key)
        return cached

    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        serialized = self._serialize(value)
        if self._redis_client:
            try:
                self._redis_client.setex(key, ttl_seconds, serialized)
                return
            except Exception:
                pass
        self._fallback_cache.set(key, serialized, ttl_seconds)


cache_manager = CacheManager()


