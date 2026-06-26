
import os
from typing import Optional

import redis as _redis


class CacheManager:

    def __init__(self, redis_url: str = ""):
        redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._r = _redis.from_url(redis_url, decode_responses=True)

# 全局单例
_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    global _manager
    if _manager is None:
        _manager = CacheManager()
    return _manager