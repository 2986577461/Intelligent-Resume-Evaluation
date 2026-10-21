"""Readiness checks: local SQLite and Redis only; never call paid model APIs."""
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

health_router = APIRouter()


@health_router.get('/api/health', include_in_schema=False)
def readiness():
    try:
        from conversation.service import APP_DB_PATH
        import redis as _redis  # 部署仍保留 Redis 探活；核心指导状态已迁到 SQLite。
        with sqlite3.connect(APP_DB_PATH, timeout=2) as conn:
            conn.execute('SELECT 1 FROM users LIMIT 1').fetchone()
        checkpoint = Path(__file__).resolve().parents[1] / 'agent/db/conversations.db'
        with sqlite3.connect(checkpoint, timeout=2) as conn:
            conn.execute('PRAGMA schema_version').fetchone()
        client = _redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=2,
                                 socket_timeout=2)
        try:
            client.ping()
        finally:
            client.close()
    except Exception:
        # Don't leak Redis URLs, filesystem paths or exception messages.
        return JSONResponse({'status': 'not_ready'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})
    return JSONResponse({'status': 'ok', 'version': os.getenv('APP_VERSION', 'local')},
                        headers={'Cache-Control': 'no-store'})
