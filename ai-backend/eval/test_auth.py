"""本地账号/会话和数据归属回归测试，不调用模型、不连接外部服务。"""

import time
from types import SimpleNamespace

import pytest

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    # 延迟导入，避免测试收集阶段初始化真实应用数据库或模型。
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from conversation import service as db
    monkeypatch.setattr(db, "APP_DB_PATH", str(tmp_path / "app.db"))
    db.init_app_db()
    from auth import service as auth
    from auth.router import auth_router
    from conversation.router import conversation_router
    from files.router import files_router
    from agent.router import agent_router
    auth.init_auth_db()
    app = FastAPI()
    for router in (auth_router, conversation_router, files_router, agent_router):
        app.include_router(router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, db=db, auth=auth)


def signup(client, email="alice@example.com"):
    response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def test_register_cookie_and_current_user(api):
    user = signup(api.client, "Alice@EXAMPLE.com")
    assert user["email"] == "alice@example.com"
    assert set(user) == {"id", "email", "name"}
    assert api.client.get("/api/auth/me").json() == user
    assert api.client.get("/api/conversations").json() == []
    conn = api.db.get_app_db()
    try:
        row = conn.execute("SELECT password_hash FROM users").fetchone()
        assert row["password_hash"].startswith("scrypt$")
        assert PASSWORD not in row["password_hash"]
        assert api.auth.verify_password(PASSWORD, row["password_hash"])
        session = conn.execute("SELECT token_hash FROM auth_sessions").fetchone()
        assert session["token_hash"] != api.client.cookies[api.auth.SESSION_COOKIE]
    finally:
        conn.close()


def test_cookie_flags(api, monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    response = api.client.post("/api/auth/register", json={"email": "alice@example.com", "password": PASSWORD})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie
    assert "max-age=604800" in cookie
    assert response.headers["cache-control"] == "no-store"


def test_duplicate_email_case_insensitive(api):
    signup(api.client)
    response = api.client.post("/api/auth/register", json={"email": "ALICE@example.com", "password": PASSWORD})
    assert response.status_code == 409


@pytest.mark.parametrize("email,password", [
    ("not-an-email", PASSWORD), ("alice@example.com", "short"),
    ("alice@example.com", "x" * 129),
])
def test_registration_validation(api, email, password):
    assert api.client.post("/api/auth/register", json={"email": email, "password": password}).status_code == 422


def test_login_logout_and_session_rotation(api):
    user = signup(api.client)
    old_token = api.client.cookies[api.auth.SESSION_COOKIE]
    response = api.client.post("/api/auth/login", json={"email": "ALICE@example.com", "password": PASSWORD})
    assert response.status_code == 200 and response.json() == user
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        api.auth.get_session_user(old_token)
    assert error.value.status_code == 401
    token = api.client.cookies[api.auth.SESSION_COOKIE]
    assert api.client.post("/api/auth/logout").status_code == 204
    assert api.client.get("/api/auth/me").status_code == 401
    # 即使恢复已退出的 Cookie，也不能继续访问。
    api.client.cookies.set(api.auth.SESSION_COOKIE, token)
    assert api.client.get("/api/conversations").status_code == 401


def test_invalid_login_uses_same_error(api):
    signup(api.client)
    wrong = api.client.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong"})
    missing = api.client.post("/api/auth/login", json={"email": "missing@example.com", "password": "wrong"})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()


def test_expired_session(api):
    signup(api.client)
    conn = api.db.get_app_db()
    try:
        conn.execute("UPDATE auth_sessions SET expires_at = ?", (int(time.time()) - 1,))
        conn.commit()
    finally:
        conn.close()
    assert api.client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("path", ["/api/conversations", "/api/files/not-found", "/chat-stream?question=hello"])
def test_protected_routes_reject_old_external_tokens(api, path):
    response = api.client.get(path, params={"token": "old-external-token"}, headers={"Authorization": "old-token"})
    assert response.status_code == 401


def test_rate_limit(api):
    for _ in range(2):
        api.auth.check_rate_limit("test", "alice@example.com", limit=2, window=60)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        api.auth.check_rate_limit("test", "alice@example.com", limit=2, window=60)
    assert error.value.status_code == 429
    assert int(error.value.headers["Retry-After"]) > 0


def test_account_isolation_for_conversations_files_and_tools(api):
    from fastapi.testclient import TestClient
    from agent.tools import _get_content_by_file_id_or_index
    alice = signup(api.client)
    thread_id, message_id = api.db.ensure_conversation_and_save_user("alice-thread", "hello", alice["id"])
    api.db.save_file_meta("alice-file", "resume.pdf", 4, thread_id, alice["id"],
                         content=b"resume", extracted_text="text")
    assert api.client.get("/api/files/alice-file").content == b"resume"
    assert api.client.get(f"/api/conversations/{thread_id}/messages").status_code == 200
    with TestClient(api.app) as other:
        bob = signup(other, "bob@example.com")
        assert other.get("/api/conversations").json() == []
        for path in (f"/api/conversations/{thread_id}/messages", "/api/files/alice-file"):
            assert other.get(path).status_code == 404
        assert other.delete(f"/api/conversations/{thread_id}").status_code == 404
        assert other.delete("/api/files/alice-file").status_code == 404
        assert _get_content_by_file_id_or_index(thread_id, "alice-file", user_id=bob["id"]) is None
        assert other.get("/chat-stream", params={"question": "hello", "thread_id": thread_id}).status_code == 404
        # 同一账号也不能从另一会话读取附件。
        assert _get_content_by_file_id_or_index("another-thread", "alice-file", user_id=alice["id"]) is None
        # 不能上传文件到别人的会话。
        assert other.post("/api/files/upload", data={"thread_id": thread_id},
                          files={"file": ("resume.pdf", b"invalid", "application/pdf")}).status_code == 404
    assert api.client.get("/api/files/alice-file").status_code == 200
    # 已绑定附件不可删除，避免破坏历史消息。
    api.db.save_message_attachment(message_id, "alice-file", "resume.pdf")
    assert api.client.delete("/api/files/alice-file").status_code == 409


def test_upload_and_delete_pdf(api):
    import fitz
    signup(api.client)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Backend engineer resume")
    data = doc.tobytes()
    doc.close()
    response = api.client.post("/api/files/upload", data={"thread_id": "new-thread"},
                               files={"file": ("resume.pdf", data, "application/pdf")})
    assert response.status_code == 200, response.text
    file_id = response.json()["file_id"]
    assert api.client.get(f"/api/files/{file_id}").content == data
    assert api.client.delete(f"/api/files/{file_id}").status_code == 200
    assert api.client.get(f"/api/files/{file_id}").status_code == 404


def test_cross_origin_requests_rejected(api):
    signup(api.client)
    assert api.client.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert api.client.get("/api/conversations", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert api.client.get("/api/auth/me").status_code == 200


def test_rate_limit_window_resets(api):
    api.auth.check_rate_limit("reset", "identity", limit=1, window=60)
    conn = api.db.get_app_db()
    try:
        conn.execute("UPDATE auth_rate_limits SET window_start = ?", (int(time.time()) - 61,))
        conn.commit()
    finally:
        conn.close()
    api.auth.check_rate_limit("reset", "identity", limit=1, window=60)


def test_passwords_are_salted_and_workers_bounded(api):
    from fastapi import HTTPException
    first = api.auth.hash_password(PASSWORD)
    second = api.auth.hash_password(PASSWORD)
    assert first != second
    assert not api.auth.verify_password("wrong", first)
    assert not api.auth.verify_password(PASSWORD, "invalid-hash")
    for _ in range(2):
        assert api.auth._PASSWORD_WORKERS.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as error:
            api.auth.hash_password(PASSWORD)
        assert error.value.status_code == 429
    finally:
        for _ in range(2):
            api.auth._PASSWORD_WORKERS.release()


def test_sse_authenticates_cookie_without_query_token(api, monkeypatch):
    import sys
    from types import ModuleType
    user = signup(api.client)
    fake_main = ModuleType("main")
    fake_main.model = object()
    fake_main.web_search = object()
    fake_service = ModuleType("agent.service")
    calls = []

    async def fake_stream(question, thread_id, user_id, **kwargs):
        calls.append((question, thread_id, user_id, kwargs))
        yield "data: hello\n\ndata: [DONE]\n\n"

    fake_service.stream_chat = fake_stream
    monkeypatch.setitem(sys.modules, "main", fake_main)
    monkeypatch.setitem(sys.modules, "agent.service", fake_service)
    response = api.client.get("/chat-stream", params={"question": "hello", "thread_id": "own-thread"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert calls[0][:3] == ("hello", "own-thread", user["id"])
    assert "token" not in calls[0][3]
    api.db.save_file_meta("other-thread-file", "resume.pdf", 4, "other-thread", user["id"],
                         extracted_text="text")
    rejected = api.client.get("/chat-stream", params={"question": "hi", "thread_id": "own-thread",
                                                      "file_id": "other-thread-file"})
    assert rejected.status_code == 404
    assert len(calls) == 1
