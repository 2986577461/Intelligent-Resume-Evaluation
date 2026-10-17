"""
会话管理模块 — 会话 CRUD、消息存储、自动标题生成、本地账号会话

本模块通过 FastAPI APIRouter 注册路由，在 Agent.py 中用 app.include_router() 挂载。
"""

import os
import sqlite3
from datetime import datetime

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

# ---- 数据库路径 ----
DB_DIR = os.getenv("APP_DATA_DIR", os.path.join(os.path.dirname(__file__), "db"))
os.makedirs(DB_DIR, exist_ok=True)
APP_DB_PATH = os.path.join(DB_DIR, "app.db")


# ============================================================
# 本地 Cookie 登录会话
# ============================================================
def get_current_user(request: Request) -> str:
    # 延迟导入避免 auth.service 与数据库初始化循环依赖。
    from auth.service import SESSION_COOKIE, get_session_user
    from common.security import require_same_origin
    require_same_origin(request)
    return get_session_user(request.cookies.get(SESSION_COOKIE, ""))["id"]


# ============================================================
# 数据库初始化
# ============================================================
def init_app_db():
    """创建 conversations / messages 表，兼容旧表自动加列"""
    conn = sqlite3.connect(APP_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
                 CREATE TABLE IF NOT EXISTS conversations
                 (
                     thread_id  TEXT PRIMARY KEY,
                     title      TEXT DEFAULT '新的对话',
                     created_at TEXT NOT NULL,
                     updated_at TEXT NOT NULL
                 )
                 """)
    conn.execute("""
                 CREATE TABLE IF NOT EXISTS messages
                 (
                     id          INTEGER PRIMARY KEY AUTOINCREMENT,
                     thread_id   TEXT NOT NULL,
                     role        TEXT NOT NULL CHECK (role IN ('user', 'ai')),
                     content     TEXT NOT NULL,
                     search_info TEXT,
                     created_at  TEXT NOT NULL,
                     FOREIGN KEY (thread_id) REFERENCES conversations (thread_id) ON DELETE CASCADE
                 )
                 """)
    conn.execute("""
                 CREATE TABLE IF NOT EXISTS uploaded_files
                 (
                     file_id        TEXT PRIMARY KEY,
                     filename       TEXT NOT NULL,
                     char_count     INTEGER DEFAULT 0,
                     user_id        TEXT DEFAULT '',
                     thread_id      TEXT DEFAULT '',
                     created_at     TEXT NOT NULL,
                     content        BLOB,
                     extracted_text TEXT,
                     page_count     INTEGER DEFAULT 1
                 )
                 """)
    try:
        conn.execute("ALTER TABLE uploaded_files ADD COLUMN extracted_text TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE uploaded_files ADD COLUMN page_count INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN user_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.execute("""
                 CREATE TABLE IF NOT EXISTS message_attachments
                 (
                     id         INTEGER PRIMARY KEY AUTOINCREMENT,
                     message_id INTEGER NOT NULL,
                     file_id    TEXT NOT NULL,
                     filename   TEXT NOT NULL,
                     FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
                 )
                 """)
    conn.commit()
    conn.close()


def get_app_db() -> sqlite3.Connection:
    """获取应用数据库连接（自动开启 WAL + 外键，Row 工厂）"""
    conn = sqlite3.connect(APP_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


# 启动时建表
init_app_db()



class CreateConversationRequest(BaseModel):
    title: str = Field(default="新的对话", description="会话标题")


# ============================================================
# 会话管理辅助函数（供 _stream_chat 使用）
# ============================================================
def ensure_conversation_and_save_user(thread_id: str, question: str, user_id: str) -> tuple[str, int]:
    """
    确保会话记录存在 + 保存用户消息，返回 (最终的 thread_id, 用户消息 id)。
    已有会话必须归当前账号所有。
    """
    require_thread_access(thread_id, user_id)
    conn = get_app_db()
    try:
        now = datetime.now().isoformat()
        existing = conn.execute(
            "SELECT thread_id, user_id FROM conversations WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO conversations (thread_id, title, created_at, updated_at, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (thread_id, "新的对话", now, now, user_id),
            )
        elif existing["user_id"] != user_id:
            raise HTTPException(404, "会话不存在")
        else:
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE thread_id = ?",
                (now, thread_id),
            )
        cur = conn.execute(
            "INSERT INTO messages (thread_id, role, content, created_at, user_id) "
            "VALUES (?, 'user', ?, ?, ?)",
            (thread_id, question, now, user_id),
        )
        conn.commit()
        return thread_id, cur.lastrowid
    finally:
        conn.close()


def save_ai_message(thread_id: str, content: str, search_info: dict | None, user_id: str) -> int:
    """保存 AI 回复到数据库，返回 message id"""
    import json
    conn = get_app_db()
    try:
        cur = conn.execute(
            "INSERT INTO messages (thread_id, role, content, search_info, created_at, user_id) "
            "VALUES (?, 'ai', ?, ?, ?, ?)",
            (thread_id, content,
             json.dumps(search_info, ensure_ascii=False) if search_info else None,
             datetime.now().isoformat(), user_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def save_file_meta(file_id: str, filename: str, char_count: int, thread_id: str,
                   user_id: str, content: bytes | None = None, extracted_text: str = "",
                   page_count: int = 1):
    conn = get_app_db()
    try:
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO uploaded_files (file_id, filename, char_count, user_id, created_at, content, thread_id, extracted_text, page_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, filename, char_count, user_id, now, content, thread_id, extracted_text, page_count),
        )
        conn.commit()
    finally:
        conn.close()


def require_thread_access(thread_id: str, user_id: str):
    """新会话允许创建，已有会话/待发送附件不能被其他账号接管。"""
    conn = get_app_db()
    try:
        row = conn.execute("SELECT user_id FROM conversations WHERE thread_id = ?", (thread_id,)).fetchone()
        foreign_file = conn.execute(
            "SELECT 1 FROM uploaded_files WHERE thread_id = ? AND user_id != ? LIMIT 1",
            (thread_id, user_id),
        ).fetchone()
        if (row and row["user_id"] != user_id) or foreign_file:
            raise HTTPException(404, "会话不存在")
    finally:
        conn.close()


def get_owned_file(file_id: str, user_id: str):
    conn = get_app_db()
    try:
        row = conn.execute("SELECT * FROM uploaded_files WHERE file_id = ? AND user_id = ?",
                           (file_id, user_id)).fetchone()
        if not row:
            raise HTTPException(404, "文件不存在")
        return row
    finally:
        conn.close()


def get_filename(file_id: str, user_id: str) -> str:
    return get_owned_file(file_id, user_id)["filename"]


def save_message_attachment(message_id: int, file_id: str, filename: str):
    conn = get_app_db()
    try:
        cur = conn.execute("""
            INSERT INTO message_attachments (message_id, file_id, filename)
            SELECT m.id, f.file_id, f.filename FROM messages m JOIN uploaded_files f
              ON f.user_id = m.user_id AND f.thread_id = m.thread_id
            WHERE m.id = ? AND f.file_id = ?
        """, (message_id, file_id))
        if cur.rowcount != 1:
            raise HTTPException(404, "文件不存在")
        conn.commit()
    finally:
        conn.close()


def get_last_insert_id(conn) -> int:
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def generate_and_save_title(thread_id: str, question: str, first_reply: str, model):
    """
    如果当前标题还是"新的对话"，用 AI 生成一个标题并写入数据库。
    model 参数是 LangChain chat model 实例。
    """
    import traceback

    # 检查是否需要生成标题
    check_conn = get_app_db()
    try:
        current = check_conn.execute(
            "SELECT title FROM conversations WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        need_title = (current and current["title"] == "新的对话")
    finally:
        check_conn.close()

    if not need_title:
        return

    generated_title = ""
    try:
        resp = model.invoke(
            f"用户问题：{question}\n"
            f"助手回答：{first_reply[:300]}\n\n"
            f"为这段对话起一个标题，15个字以内"
        )
        generated_title = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
        if len(generated_title) > 30:
            generated_title = generated_title[:30]
    except Exception:
        traceback.print_exc()

    if generated_title:
        conn = get_app_db()
        try:
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE thread_id = ?",
                (generated_title, datetime.now().isoformat(), thread_id),
            )
            conn.commit()
        finally:
            conn.close()