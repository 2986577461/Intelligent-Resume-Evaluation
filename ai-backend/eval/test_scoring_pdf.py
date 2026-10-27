"""真实上传/工具/持久化续评的离线回归，不发送本地简历到网络。"""

import json
import os
from pathlib import Path

import pytest

from eval.test_auth import api, signup
from eval.test_offline_pipeline import (CONTEXT, EXTRACTION, RESUME, REVIEW, SYNTHESIS,
                                       MemoryCache, ScriptedModel, signal)


def pdf_bytes():
    path = os.getenv("SCORING_TEST_PDF")
    if path:
        return Path(path).read_bytes()
    import fitz
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Synthetic backend resume: Python Redis retrieval project")
        return doc.tobytes()


def upload(api):
    user = signup(api.client)
    response = api.client.post("/api/files/upload", data={"thread_id": "pdf-thread"},
                               files={"file": ("resume.pdf", pdf_bytes(), "application/pdf")})
    assert response.status_code == 200
    file_id = response.json()["file_id"]
    # PDF 提取路径确实执行，但假模型输出仅能引用合成文本；不声称验证真实 PDF 的语义。
    row = api.db.get_owned_file(file_id, user["id"])
    assert row["page_count"] > 0 and row["extracted_text"].strip()
    conn = api.db.get_app_db()
    try:
        conn.execute("UPDATE uploaded_files SET extracted_text=? WHERE file_id=?", (RESUME, file_id))
        conn.commit()
    finally:
        conn.close()
    return user["id"], file_id


def configure(monkeypatch, model, cache=None):
    monkeypatch.setattr("agent.scoring.create_scoring_model", lambda: model)
    monkeypatch.setattr("state.manager.get_cache_manager", lambda: cache)


def test_pdf_upload_tool_state_cache_and_file_access(api, monkeypatch):
    from agent.tools import analyze_resume
    from agent.guidance_state import GuidanceStore
    model, cache = ScriptedModel(), MemoryCache()
    configure(monkeypatch, model, cache)
    user, file_id = upload(api)
    chunks, metrics = [], []
    config = {"configurable": {"user_id": user, "thread_id": "pdf-thread",
                               "emit_chunk": chunks.append, "record_metric": metrics.append}}
    report = analyze_resume.invoke({"file_id": file_id}, config=config)
    assert "".join(chunks) == report and len(chunks) > 5 and "总体判断" in report and "总分" not in report
    chunks.clear()
    assert "优先修改清单" in analyze_resume.invoke({"file_id": file_id}, config=config)
    assert sum(name == "extraction" for name, _ in model.calls) == 1
    assert any(m.get("event") == "cache" and m.get("hit") for m in metrics)
    assert GuidanceStore().load(user, "pdf-thread")["status"] == "ready"
    count = len(model.calls)
    for invalid in [
        {"configurable": {"user_id": user, "thread_id": "other"}},
        {"configurable": {"user_id": "other", "thread_id": "pdf-thread"}},
    ]:
        assert "未找到" in analyze_resume.invoke({"file_id": file_id}, config=invalid)
    assert api.client.delete(f"/api/files/{file_id}").status_code == 200
    assert GuidanceStore().load(user, "pdf-thread") is None
    assert "未找到" in analyze_resume.invoke({}, config=config)
    assert "未找到" in analyze_resume.invoke({"file_id": file_id}, config=config)
    assert len(model.calls) == count


def test_tool_resumes_without_file_argument_after_restart_and_redis_failure(api, monkeypatch):
    from agent.tools import analyze_resume
    from agent.guidance_state import GuidanceStore
    user, file_id = upload(api)
    first = ScriptedModel(contexts=[{"position": signal("后端", "后端", "user")}])
    configure(monkeypatch, first)
    config = {"configurable": {"user_id": user, "thread_id": "pdf-thread", "user_message": "投后端"}}
    assert "实习、校招还是社招" in analyze_resume.invoke({"file_id": file_id}, config=config)
    assert GuidanceStore().load(user, "pdf-thread")["status"] == "waiting"
    second = ScriptedModel(contexts=[{"application_type": signal("校招", "校招", "user")}])
    configure(monkeypatch, second)
    monkeypatch.setattr("state.manager.get_cache_manager", lambda: (_ for _ in ()).throw(ConnectionError("offline")))
    config["configurable"]["user_message"] = "校招"
    result = analyze_resume.invoke({}, config=config)
    assert "目标岗位：后端｜投递类型：校招" in result
    assert "extraction" not in [name for name, _ in second.calls]
    assert GuidanceStore().load(user, "pdf-thread")["status"] == "ready"


def test_new_attachment_wins_over_stale_model_file_id(api, monkeypatch):
    from agent.tools import analyze_resume
    from agent.guidance_state import GuidanceStore
    user, file_id = upload(api)
    api.db.save_file_meta("new-file", "new.pdf", len(RESUME), "pdf-thread", user, extracted_text=RESUME)
    model = ScriptedModel()
    configure(monkeypatch, model)
    result = analyze_resume.invoke({"file_id": file_id}, config={"configurable": {
        "user_id": user, "thread_id": "pdf-thread", "file_id": "new-file"}})
    assert "总体判断" in result
    assert GuidanceStore().load(user, "pdf-thread")["file_id"] == "new-file"


def test_persisted_state_cannot_cross_accounts_or_conversations(api):
    from agent.guidance_state import GuidanceStore
    user, file_id = upload(api)
    store = GuidanceStore()
    store.save(user, "pdf-thread", file_id, "key", EXTRACTION, CONTEXT, "waiting")
    assert store.load("bob", "pdf-thread") is None
    assert store.load(user, "other") is None
    with pytest.raises(ValueError):
        store.save("bob", "pdf-thread", file_id, "key", EXTRACTION, CONTEXT, "waiting")
    api.db.ensure_conversation_and_save_user("pdf-thread", "hi", user)
    assert api.client.delete("/api/conversations/pdf-thread").status_code == 200
    assert store.load(user, "pdf-thread") is None


@pytest.mark.parametrize("transient", [False, True])
def test_pdf_real_sdk_json_mode_timeout_and_retry(api, monkeypatch, transient):
    pytest.importorskip("langchain_deepseek")
    import httpx
    from langchain.chat_models import init_chat_model as real_init
    from agent.tools import analyze_resume
    requests = []
    fault_sent = False

    def respond(request):
        nonlocal fault_sent
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "disabled"}
        assert request.extensions["timeout"]["read"] == 17
        prompt = body["messages"][0]["content"]
        stage = next((key for key, word in {
            "extraction": "简历事实提取员", "context": "识别本轮投递上下文",
            "skill": "岗位需要什么—技能声称什么", "project": "负责审阅项目证据",
            "experience": "负责审阅实习与工作证据", "education": "负责核对教育背景",
            "synthesis": "生成面向投递者的简历指导",
        }.items() if word in prompt), None)
        assert stage is not None
        requests.append(stage)
        if transient and stage == "project" and not fault_sent:
            fault_sent = True
            return httpx.Response(429, json={"error": {"message": "synthetic rate limit", "type": "rate_limit"}})
        value = (EXTRACTION if stage == "extraction" else CONTEXT if stage == "context" else
                 SYNTHESIS if stage == "synthesis" else REVIEW)
        if stage == "synthesis":
            assert body["stream"] is True
            text = json.dumps(value, ensure_ascii=False)
            events = []
            for index in range(0, len(text), 29):
                events.append("data: " + json.dumps({
                    "id": "mock", "object": "chat.completion.chunk", "created": 1, "model": "deepseek-v4-flash",
                    "choices": [{"index": 0, "delta": {"content": text[index:index + 29]}, "finish_reason": None}],
                }, ensure_ascii=False) + "\n\n")
            events.append('data: {"id":"mock","object":"chat.completion.chunk","created":1,"model":"deepseek-v4-flash","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
            events.append("data: [DONE]\n\n")
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(events).encode())
        return httpx.Response(200, json={
            "id": "mock", "object": "chat.completion", "created": 1, "model": "deepseek-v4-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(value, ensure_ascii=False)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr("langchain.chat_models.init_chat_model", lambda **kwargs: real_init(**kwargs, http_client=client))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-dummy-key")
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://offline.invalid/v1")
        monkeypatch.setenv("MODEL_NAME", "deepseek-v4-flash")
        monkeypatch.setenv("SCORING_REQUEST_TIMEOUT_SECONDS", "17")
        monkeypatch.setattr("state.manager.get_cache_manager", lambda: MemoryCache())
        user, file_id = upload(api)
        metrics = []
        report = analyze_resume.invoke({"file_id": file_id}, config={"configurable": {
            "user_id": user, "thread_id": "pdf-thread", "record_metric": metrics.append,
        }})
    assert "## 总体判断" in report
    assert len(requests) == (8 if transient else 7)
    assert requests.index("skill") > max(i for i, name in enumerate(requests) if name in {"project", "experience"})
    assert requests[-1] == "synthesis"
    assert max(m.get("attempt", 0) for m in metrics) == (2 if transient else 1)
