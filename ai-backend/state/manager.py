
import hashlib
import json
import os
from typing import Optional

_PENDING_EVAL_PREFIX = "resume_eval:pending:"
_EXTRACTION_PREFIX = "resume_eval:extraction:v1:"


class CacheManager:

    def __init__(self, redis_url: str = ""):
        import redis as _redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._r = _redis.from_url(redis_url, decode_responses=True,
                                  socket_connect_timeout=1, socket_timeout=1,
                                  retry=Retry(NoBackoff(), 0), retry_on_timeout=False)

    @staticmethod
    def extraction_key(user_id, thread_id, file_id, text, version):
        # 不把个人信息放入 Redis key；隔离用户/文件/会话，内容或协议变化即失效。
        raw = json.dumps([user_id, thread_id, file_id, text, version], ensure_ascii=False)
        return _EXTRACTION_PREFIX + hashlib.sha256(raw.encode()).hexdigest()

    def get_extraction(self, key):
        raw = self._r.get(key)
        return json.loads(raw) if raw else None

    def set_extraction(self, key, extracted, ttl=3600):
        self._r.set(key, json.dumps(extracted, ensure_ascii=False), ex=ttl)

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