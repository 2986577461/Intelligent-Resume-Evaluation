"""邮箱账号保存在 SQLite；密码使用 scrypt，Cookie 会话只存摘要。"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
import uuid

from fastapi import HTTPException

from conversation.service import get_app_db

SESSION_COOKIE = "resume_session"
SESSION_TTL = 7 * 24 * 60 * 60
_SCRYPT_N = 2**17
_PASSWORD_WORKERS = threading.BoundedSemaphore(2)
_DUMMY_HASH = f"scrypt${_SCRYPT_N}$8$1${'00' * 16}${'00' * 64}"


def init_auth_db():
    conn = get_app_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);
            CREATE TABLE IF NOT EXISTS auth_rate_limits (
                bucket_key TEXT PRIMARY KEY,
                window_start INTEGER NOT NULL,
                attempts INTEGER NOT NULL
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _derive_password(password: str, salt: bytes) -> bytes:
    # 单进程最多两个 128 MiB 密码计算，避免并发请求耗尽内存。
    if not _PASSWORD_WORKERS.acquire(blocking=False):
        raise HTTPException(429, "登录服务繁忙，请稍后再试", headers={"Retry-After": "1"})
    try:
        return hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N,
                              r=8, p=1, maxmem=256 * 1024 * 1024)
    finally:
        _PASSWORD_WORKERS.release()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive_password(password, salt)
    return f"scrypt${_SCRYPT_N}$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", str(_SCRYPT_N), "8", "1"):
            return False
        digest = _derive_password(password, bytes.fromhex(salt))
        return hmac.compare_digest(digest, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def public_user(row) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row["email"].split("@", 1)[0]}


def register_user(email: str, password: str) -> dict:
    email = email.strip().casefold()
    password_hash = hash_password(password)
    user_id = "user_" + uuid.uuid4().hex
    conn = get_app_db()
    try:
        try:
            conn.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                         (user_id, email, password_hash, int(time.time())))
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "该邮箱已注册，请直接登录") from None
        return public_user({"id": user_id, "email": email})
    finally:
        conn.close()


def authenticate(email: str, password: str) -> dict:
    conn = get_app_db()
    try:
        row = conn.execute("SELECT id, email, password_hash FROM users WHERE email = ?",
                           (email.strip().casefold(),)).fetchone()
    finally:
        conn.close()
    # 不存在的账号也执行同等成本的散列，避免通过时间差枚举邮箱。
    valid = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
    if not row or not valid:
        raise HTTPException(401, "邮箱或密码错误")
    return public_user(row)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    conn = get_app_db()
    try:
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (int(time.time()),))
        conn.execute("INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (_token_hash(token), user_id, int(time.time()) + SESSION_TTL))
        conn.commit()
    finally:
        conn.close()
    return token


def get_session_user(token: str) -> dict:
    if not token or len(token) > 128:
        raise HTTPException(401, "请先登录")
    conn = get_app_db()
    try:
        row = conn.execute("""
            SELECT u.id, u.email FROM auth_sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?
        """, (_token_hash(token), int(time.time()))).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(401, "登录已失效，请重新登录")
    return public_user(row)


def revoke_session(token: str):
    if not token:
        return
    conn = get_app_db()
    try:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()
    finally:
        conn.close()


def cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"true", "1", "yes"}


def check_rate_limit(scope: str, identity: str, limit: int, window: int):
    """SQLite 原子计数，多进程共享；只持久化邮箱/IP 的摘要。"""
    now = int(time.time())
    key = scope + ":" + hashlib.sha256(identity.encode()).hexdigest()
    conn = get_app_db()
    try:
        row = conn.execute("""
            INSERT INTO auth_rate_limits (bucket_key, window_start, attempts) VALUES (?, ?, 1)
            ON CONFLICT(bucket_key) DO UPDATE SET
                attempts = CASE WHEN window_start <= ? THEN 1 ELSE attempts + 1 END,
                window_start = CASE WHEN window_start <= ? THEN excluded.window_start ELSE window_start END
            RETURNING attempts, window_start
        """, (key, now, now - window, now - window)).fetchone()
        conn.execute("DELETE FROM auth_rate_limits WHERE window_start < ?", (now - 86400,))
        conn.commit()
    finally:
        conn.close()
    if row["attempts"] > limit:
        retry_after = max(1, row["window_start"] + window - now)
        raise HTTPException(429, "尝试过于频繁，请稍后再试", headers={"Retry-After": str(retry_after)})


init_auth_db()
