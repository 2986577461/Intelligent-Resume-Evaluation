"""真实上传/工具路径离线回归；指定 PDF 只读本地，不把文本发送到网络。"""

import json
import os
from pathlib import Path

import pytest

from eval.test_auth import api, signup
from eval.test_offline_pipeline import EXTRACTION, SYNTHESIS, MemoryCache, ScriptedModel


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
    return user["id"], file_id


def test_pdf_upload_tool_cache_and_file_access(api, monkeypatch):
    from agent.tools import analyze_resume
    model, cache = ScriptedModel(), MemoryCache()
    monkeypatch.setattr("agent.scoring.create_scoring_model", lambda: model)
    monkeypatch.setattr("state.manager.get_cache_manager", lambda: cache)
    user, file_id = upload(api)
    row = api.db.get_owned_file(file_id, user)
    assert row["page_count"] > 0 and row["extracted_text"].strip()
    chunks, metrics = [], []
    config = {"configurable": {"user_id": user, "thread_id": "pdf-thread",
                               "emit_chunk": chunks.append, "record_metric": metrics.append}}
    report = analyze_resume.invoke({"file_id": file_id}, config=config)
    assert chunks == [report] and "总分" in report
    assert "## 优势" in report and "## 总结" in report
    assert "# 简历评估报告" not in report
    chunks.clear()
    assert "总分" in analyze_resume.invoke({"file_id": file_id}, config=config)
    assert sum(name == "extraction" for name, _ in model.calls) == 1
    assert any(m.get("event") == "cache" and m.get("hit") for m in metrics)
    count = len(model.calls)
    wrong_thread = {"configurable": {"user_id": user, "thread_id": "other"}}
    wrong_user = {"configurable": {"user_id": "other", "thread_id": "pdf-thread"}}
    for invalid in [wrong_thread, wrong_user]:
        assert "未找到" in analyze_resume.invoke({"file_id": file_id}, config=invalid)
    assert api.client.delete(f"/api/files/{file_id}").status_code == 200
    assert "未找到" in analyze_resume.invoke({"file_id": file_id}, config=config)
    assert len(model.calls) == count  # 缓存不能绕过所属检查/文件删除。


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
            "extraction": "你是一个简历分析师", "skill": "技能匹配度评估专家",
            "project": "项目经验评估专家", "experience": "经验背景评估专家",
            "education": "学历背景评估专家", "synthesis": "简历评估报告生成专家",
        }.items() if word in prompt), None)
        assert stage is not None
        requests.append(stage)
        if transient and stage == "project" and not fault_sent:
            fault_sent = True
            return httpx.Response(429, json={"error": {"message": "synthetic rate limit", "type": "rate_limit"}})
        value = (EXTRACTION if stage == "extraction" else SYNTHESIS if stage == "synthesis" else
                 {"raw_score": {"skill": 80, "project": 70, "experience": 35, "education": 60}[stage],
                  "detail": ["示例检索项目使用 Python"]})
        return httpx.Response(200, json={
            "id": "mock", "object": "chat.completion", "created": 1, "model": "deepseek-v4-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(value, ensure_ascii=False)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        def init(**kwargs):
            return real_init(**kwargs, http_client=client)
        monkeypatch.setattr("langchain.chat_models.init_chat_model", init)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-dummy-key")
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://offline.invalid/v1")
        monkeypatch.setenv("MODEL_NAME", "deepseek-v4-flash")
        monkeypatch.setenv("SCORING_REQUEST_TIMEOUT_SECONDS", "17")
        monkeypatch.setattr("state.manager.get_cache_manager", MemoryCache)
        user, file_id = upload(api)
        metrics = []
        report = analyze_resume.invoke({"file_id": file_id}, config={"configurable": {
            "user_id": user, "thread_id": "pdf-thread", "record_metric": metrics.append,
        }})
    assert "## 总结" in report
    assert len(requests) == (7 if transient else 6)
    assert requests.index("skill") > max(i for i, name in enumerate(requests) if name == "project")
    assert requests[-1] == "synthesis" and "layout" not in requests
    assert max(m.get("attempt", 0) for m in metrics) == (2 if transient else 1)
