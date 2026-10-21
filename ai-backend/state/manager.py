"""单实例文件级指导存储：与应用 SQLite 共生命周期，不借用聊天 checkpoint。

原 Redis pending 缓存已迁移；v1 不读取旧评分提取格式。
"""
import hashlib
from contextlib import contextmanager

from fastapi import HTTPException

from agent.schema import Extraction, Result
from conversation.service import get_app_db, get_owned_file, require_thread_access


@contextmanager
def connection():
    conn = get_app_db()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class GuidanceStore:
    def __init__(self):
        with connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS guidance_extractions (
                    file_id TEXT PRIMARY KEY REFERENCES uploaded_files(file_id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL,
                    extraction TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS guidance_results (
                    id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL REFERENCES uploaded_files(file_id) ON DELETE CASCADE,
                    result TEXT NOT NULL
                );
            """)

    def owned_file(self, user_id, thread_id, file_id):
        require_thread_access(thread_id, user_id)
        row = get_owned_file(file_id, user_id)
        if row["thread_id"] != thread_id:
            raise HTTPException(404, "文件不存在")
        return row

    @staticmethod
    def fingerprint(text):
        return "guidance.v1:" + hashlib.sha256(text.encode()).hexdigest()

    def extraction(self, file_id, text):
        with connection() as conn:
            row = conn.execute("SELECT * FROM guidance_extractions WHERE file_id = ?", (file_id,)).fetchone()
        if row and row["fingerprint"] == self.fingerprint(text):
            return Extraction.model_validate_json(row["extraction"])
        return None

    def save_extraction(self, file_id, text, extraction):
        with connection() as conn:
            conn.execute("INSERT OR REPLACE INTO guidance_extractions VALUES (?, ?, ?)",
                         (file_id, self.fingerprint(text), extraction.model_dump_json()))

    def result(self, result_id, user_id, thread_id, file_id):
        self.owned_file(user_id, thread_id, file_id)
        with connection() as conn:
            row = conn.execute("SELECT result FROM guidance_results WHERE id = ? AND file_id = ?",
                               (result_id, file_id)).fetchone()
        if not row:
            raise HTTPException(404, "分析记录不存在或不属于此文件")
        return Result.model_validate_json(row["result"])

    def save_result(self, result):
        # 存储边界复核结构与引用；失败/不完整记录保留其真实状态。
        from agent.nodes import validate_extraction, validate_guidance
        checked = Result.model_validate_json(result.model_dump_json())
        if checked.facts:
            validate_extraction(Extraction(facts=checked.facts), checked.sources)
        if checked.guidance:
            validate_guidance(checked.guidance, checked.sources)
        with connection() as conn:
            conn.execute("INSERT INTO guidance_results VALUES (?, ?, ?)",
                         (checked.id, checked.file_id, checked.model_dump_json()))
