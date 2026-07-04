
import json
import os
from typing import Optional

import redis as _redis

_PENDING_EVAL_PREFIX = "resume_eval:pending:"


class CacheManager:

    def __init__(self, redis_url: str = ""):
        redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._r = _redis.from_url(redis_url, decode_responses=True)

    def set_pending_evaluation(self, file_id: str, extracted: dict, ttl: int = 3600):
        """简历分析因缺少 position 中断时，缓存已提取的结构化信息，等用户补充岗位后续跑"""
        self._r.set(f"{_PENDING_EVAL_PREFIX}{file_id}", json.dumps(extracted, ensure_ascii=False), ex=ttl)

    def get_pending_evaluation(self, file_id: str) -> Optional[dict]:
        raw = self._r.get(f"{_PENDING_EVAL_PREFIX}{file_id}")
        return json.loads(raw) if raw else None

    def delete_pending_evaluation(self, file_id: str):
        self._r.delete(f"{_PENDING_EVAL_PREFIX}{file_id}")

# 全局单例
_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    global _manager
    if _manager is None:
        _manager = CacheManager()
    return _manager