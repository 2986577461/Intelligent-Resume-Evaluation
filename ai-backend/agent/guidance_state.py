"""本地持久化续评状态，Redis 故障不丢失用户已确认信息。"""

import json
from datetime import datetime

from conversation.service import get_app_db


class GuidanceStore:
    def load(self, user_id, thread_id):
        conn = get_app_db()
        try:
            # 每次恢复重新校验文件归属；文件删除时外键也会清除状态。
            row = conn.execute(
                "SELECT g.* FROM resume_guidance_contexts g JOIN uploaded_files f ON f.file_id=g.file_id "
                "WHERE g.user_id=? AND g.thread_id=? AND f.user_id=? AND f.thread_id=?",
                (user_id, thread_id, user_id, thread_id),
            ).fetchone()
            if not row:
                return None
            return {**dict(row), "extraction": json.loads(row["extraction"]), "context": json.loads(row["context"])}
        finally:
            conn.close()

    def save(self, user_id, thread_id, file_id, extraction_key, extraction, context, status):
        conn = get_app_db()
        try:
            # 不允许调用者凭一个 file_id 写入别人的文件状态。
            owned = conn.execute("SELECT 1 FROM uploaded_files WHERE file_id=? AND user_id=? AND thread_id=?",
                                 (file_id, user_id, thread_id)).fetchone()
            if not owned:
                raise ValueError("文件不存在")
            conn.execute(
                "INSERT INTO resume_guidance_contexts "
                "(user_id,thread_id,file_id,extraction_key,extraction,context,status,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id,thread_id) DO UPDATE SET file_id=excluded.file_id, "
                "extraction_key=excluded.extraction_key,extraction=excluded.extraction,context=excluded.context, "
                "status=excluded.status,updated_at=excluded.updated_at",
                (user_id, thread_id, file_id, extraction_key, json.dumps(extraction, ensure_ascii=False),
                 json.dumps(context, ensure_ascii=False), status, datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
