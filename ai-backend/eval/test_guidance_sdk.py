"""真实 LangChain/DeepSeek/OpenAI SDK + mock HTTP，避免只测 ControlledModel。

真实 PDF 仅在 GUIDANCE_TEST_PDF 指定时读取；live 测试还需显式同意费用。
不加载 .env，不连接真实应用 DB，不打印简历/凭证。
"""
import json
import os
import time
from pathlib import Path

import httpx
import pytest

from agent import guidance_service
from agent.schema import Result
from eval.test_auth import api, signup


def sdk_transport(monkeypatch, handler):
    chat_models = pytest.importorskip("langchain.chat_models")
    pytest.importorskip("langchain_deepseek")
    original = chat_models.init_chat_model
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-dummy-not-a-real-key")
    monkeypatch.setenv("MODEL_NAME", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://configured-gateway.invalid/v1/")
    monkeypatch.setenv("GUIDANCE_REQUEST_TIMEOUT_SECONDS", "7")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(chat_models, "init_chat_model", lambda **kw: original(**kw, http_client=client))
    return client


def completed(content):
    return httpx.Response(200, json={"id": "offline-sdk", "object": "chat.completion", "created": 0,
        "model": "deepseek-v4-flash", "choices": [{"index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}}]})


def guided_reply(request):
    wire = json.loads(request.content)
    payload = json.loads(wire["messages"][-1]["content"])
    src = next(s for s in payload["sources"] if s["kind"] == "resume")
    if "facts" not in payload:
        return completed({"facts": [{"kind": "other", "source_id": src["id"], "quote": src["text"]}]})
    return completed({
        "matches": [{"id": "r1", "requirement": "说明实际实现与验证方法", "basis": "general",
            "requirement_source_ids": [], "evidence_source_ids": [], "status": "insufficient",
            "observation": "需要结合具体职责与验证过程进一步核对", "rationale": "原文存在不代表项目结果已经独立验证"}],
        "actions": [{"priority": 1, "requirement_id": "r1", "source_ids": [src["id"]],
            "problem": "需核对个人实现范围和可复现验证过程", "rationale": "不能从项目名称直接推断技术深度",
            "next_step": "选择一项自己实现的功能，补充输入、处理流程、异常处理和本地验证步骤；没有实测数据时不要添加指标"}],
        "questions": [{"id": "q1", "requirement_id": "r1", "question": "请说明一项自己实现的功能及验证步骤。",
                       "why": "确认职责范围后再做局部改写"}], "rewrites": [], "limitations": []})


def upload(api, path=None):
    signup(api.client)
    if path:
        raw = Path(path).read_bytes()
    else:
        import fitz
        with fitz.open() as doc:
            doc.new_page().insert_text((72, 72), "Project: Python API with tests.")
            raw = doc.tobytes()
    response = api.client.post("/api/files/upload", data={"thread_id": "pdf-sdk-test"},
        files={"file": ("test-resume.pdf", raw, "application/pdf")})
    assert response.status_code == 200, "PDF 上传/解析失败"
    return response.json()["file_id"]


def submit(api, file_id):
    return api.client.post("/api/resume-guidance", json={"thread_id": "pdf-sdk-test", "file_id": file_id,
        "target_role": os.getenv("GUIDANCE_TEST_ROLE", "AI智能应用开发实习生")})


def test_sdk_configured_endpoint_json_timeout_and_pdf_report(api, monkeypatch, tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://configured-gateway.invalid/v1/chat/completions"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "disabled"}
        assert body["model"] == "deepseek-v4-flash"
        assert 0 < request.extensions["timeout"]["read"] <= 7
        return guided_reply(request)
    with sdk_transport(monkeypatch, handler):
        file_id = upload(api, os.getenv("GUIDANCE_TEST_PDF"))
        response = submit(api, file_id)
        assert response.status_code == 200
        data = response.json()
        result = Result.model_validate(data["result"])
        assert result.status == "partial" and result.error_code is None and result.guidance.actions
        assert len(calls) == 2  # 一次事实提取，一次岗位分析；无隐藏 SDK 重试。
        assert "按优先级行动" in data["markdown"] and "处理状态：" not in data["markdown"]
        history = api.client.get("/api/conversations/pdf-sdk-test/messages").json()
        assert history[-1]["content"] == data["markdown"]
        assert json.loads(history[-1]["search_info"])["guidance"] == result.model_dump()
        (tmp_path / "report.md").write_text("离线 SDK 集成测试（HTTP 返回受控样本，不是真实模型结论）\n\n" + data["markdown"])
        print(f"PDF/SDK smoke: status={result.status}, calls={len(calls)}, report={tmp_path / 'report.md'}")


@pytest.mark.parametrize("status,code", [(400, "request_rejected"), (401, "authentication"), (402, "quota"),
    (403, "permission"), (404, "model_not_found"), (413, "input_too_large"), (422, "request_rejected")])
def test_real_sdk_http_errors_are_actionable_safe_and_not_retried(api, monkeypatch, caplog, status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "SECRET_RESUME_OR_KEY_MUST_NOT_LEAK",
                                                     "type": "provider_error", "code": "error"}})
    with sdk_transport(monkeypatch, handler):
        response = submit(api, upload(api))
        result = Result.model_validate(response.json()["result"])
        assert result.status == "failed" and result.error_code == code
        assert result.processing_error.http_status == status and result.processing_error.stage == "extract"
        assert result.processing_error.attempts == 1 and len(calls) == 1
        assert "SECRET_RESUME_OR_KEY_MUST_NOT_LEAK" not in response.text + caplog.text
        assert "处理未完成的原因" in response.json()["markdown"]
        assert result.processing_error.exception_type in caplog.text


def test_missing_model_key_is_configuration_not_generic_error(api, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    response = submit(api, upload(api))
    result = Result.model_validate(response.json()["result"])
    assert result.status == "failed" and result.error_code == "configuration"
    assert result.processing_error.stage == "model_init" and result.processing_error.attempts == 0
    from agent.render import safe
    assert safe("未配置 DEEPSEEK_API_KEY") in response.json()["markdown"]


@pytest.mark.parametrize("status,code", [(429, "rate_limit"), (503, "network")])
def test_sdk_transient_errors_have_only_business_retries(api, monkeypatch, status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "transient", "type": "server_error"}})
    with sdk_transport(monkeypatch, handler):
        response = submit(api, upload(api))
        result = Result.model_validate(response.json()["result"])
        assert result.error_code == code and result.processing_error.attempts == 2
        assert len(calls) == 2


@pytest.mark.parametrize("exception_name,code", [("ReadTimeout", "timeout"), ("ConnectError", "network")])
def test_real_sdk_wrapped_transport_failures_are_retried(api, monkeypatch, exception_name, code, caplog):
    calls = []
    def handler(request):
        calls.append(request)
        raise getattr(httpx, exception_name)("PRIVATE_TRANSPORT_ERROR_MUST_NOT_LEAK", request=request)
    with sdk_transport(monkeypatch, handler):
        response = submit(api, upload(api))
        result = Result.model_validate(response.json()["result"])
        assert result.status == "failed" and result.error_code == code
        assert result.processing_error.attempts == 2 and len(calls) == 2
        assert result.processing_error.exception_type in {"OpenAITimeoutError", "OpenAIConnectionError",
                                                        "APITimeoutError", "APIConnectionError"}
        assert "PRIVATE_TRANSPORT_ERROR_MUST_NOT_LEAK" not in response.text + caplog.text


def test_sdk_analysis_rejection_preserves_extraction_and_next_request_reuses_it(api, monkeypatch):
    reject_guide = [True]
    calls = []
    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        calls.append("guide" if "facts" in payload else "extract")
        if "facts" in payload and reject_guide[0]:
            return httpx.Response(400, json={"error": {"message": "unsupported model parameter", "type": "invalid_request_error"}})
        return guided_reply(request)
    with sdk_transport(monkeypatch, handler):
        file_id = upload(api)
        first = Result.model_validate(submit(api, file_id).json()["result"])
        assert first.status == "partial" and first.facts and first.guidance is None
        assert first.error_code == "request_rejected" and first.processing_error.stage == "guide"
        reject_guide[0] = False
        second = Result.model_validate(submit(api, file_id).json()["result"])
        assert second.error_code is None and second.guidance.actions
        assert first.facts == second.facts and calls == ["extract", "guide", "guide"]


@pytest.mark.llm
def test_live_pdf_guidance(api, tmp_path):
    if os.getenv("GUIDANCE_PDF_LIVE") != "1":
        pytest.skip("需 GUIDANCE_PDF_LIVE=1 显式允许真实模型调用与费用")
    if not os.getenv("DEEPSEEK_API_KEY") or not os.getenv("GUIDANCE_TEST_PDF"):
        pytest.fail("需由运行环境提供 DEEPSEEK_API_KEY 与 GUIDANCE_TEST_PDF；不会自动加载 .env")
    started = time.monotonic()
    response = submit(api, upload(api, os.environ["GUIDANCE_TEST_PDF"]))
    assert response.status_code == 200
    data = response.json()
    result = Result.model_validate(data["result"])
    (tmp_path / "result.json").write_text(result.model_dump_json(indent=2))
    (tmp_path / "report.md").write_text(data["markdown"])
    elapsed = time.monotonic() - started
    print(f"Live PDF: status={result.status}, error={result.error_code}, elapsed={elapsed:.1f}s, artifacts={tmp_path}")
    assert result.error_code is None, f"{result.error_code}: {result.processing_error}"
    assert result.guidance is not None and result.guidance.actions
    assert result.status in {"success", "partial"}
    assert all(any(s.id == f.source_id and f.quote in s.text for s in result.sources) for f in result.facts)
    history = api.client.get("/api/conversations/pdf-sdk-test/messages").json()
    assert history[-1]["content"] == data["markdown"]
    assert json.loads(history[-1]["search_info"])["guidance"] == result.model_dump()
    fetched = api.client.get(f"/api/resume-guidance/{result.file_id}/{result.id}", params={"thread_id": "pdf-sdk-test"})
    assert fetched.status_code == 200 and fetched.json() == result.model_dump()
    summary = {"status": result.status, "error_code": result.error_code, "elapsed_seconds": round(elapsed, 2),
               "facts": len(result.facts), "matches": len(result.guidance.matches),
               "actions": len(result.guidance.actions), "questions": len(result.guidance.questions),
               "rewrites": len(result.guidance.rewrites), "exact_quotes_verified": True,
               "history_and_result_read_verified": True}
    (tmp_path / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
