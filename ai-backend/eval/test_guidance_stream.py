"""真实 SSE 服务 + 临时 SQLite + 假路由/假模型，检查续评接线，不验证 LLM 意图识别。"""

import asyncio
import ast

import pytest

from eval.test_auth import api
from eval.test_offline_pipeline import SYNTHESIS, ScriptedModel, signal
from eval.test_scoring_pdf import configure, upload


@pytest.mark.parametrize("incomplete", [False, True])
def test_stream_preserves_raw_message_and_exposes_pending_state(api, monkeypatch, tmp_path, incomplete):
    pytest.importorskip("langchain")
    from agent import service
    from agent.tools import analyze_resume
    from langchain_core.messages import AIMessageChunk, ToolMessage

    user, file_id = upload(api)
    captured = []
    model = ScriptedModel(contexts=[
        {"position": signal("后端", "后端", "user")},
        {"application_type": signal("校招", "校招", "user")},
    ])
    if incomplete:
        model.faults["synthesis"] = [{**SYNTHESIS, "sections": []}]
    configure(monkeypatch, model)
    monkeypatch.setattr(service, "DB_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setattr("conversation.service.generate_and_save_title", lambda *args: None)

    class FakeRouter:
        def stream(self, inputs, config, stream_mode):
            captured.append((ast.literal_eval(inputs["messages"][0]["content"]), config))
            yield AIMessageChunk(content="", tool_call_chunks=[
                {"name": "analyze_resume", "args": "{}", "id": "call-guidance", "index": 0},
            ]), {}
            result = analyze_resume.invoke({}, config=config)
            yield ToolMessage(content=result, tool_call_id="call-guidance"), {}

    monkeypatch.setattr(service, "create_agent", lambda **kwargs: FakeRouter())

    async def collect(message, attached=""):
        return [chunk async for chunk in service.stream_chat(
            message, "pdf-thread", user, model=object(), tools=[analyze_resume], file_id=attached)]

    first = "帮我看看简历，投后端"
    events = asyncio.run(collect(first, file_id))
    assert any("实习、校招还是社招" in event for event in events)
    assert captured[0][0]["file_id"] == file_id
    assert captured[0][1]["configurable"]["user_message"] == first
    events = asyncio.run(collect("校招"))
    assert events[-1] == "data: [DONE]\n\n"
    assert sum("## 总体判断" in event for event in events) == 1
    pending = captured[1][0]["pending_analysis"]
    assert pending == {"file_id": file_id, "missing_fields": ["application_type"]}
    assert captured[1][1]["configurable"]["user_message"] == "校招"
    assert sum(stage == "extraction" for stage, _ in model.calls) == 1
    conn = api.db.get_app_db()
    try:
        messages = conn.execute("SELECT role, content FROM messages ORDER BY id").fetchall()
        assert [row["role"] for row in messages] == ["user", "ai", "user", "ai"]
        assert "实习、校招还是社招" in messages[1]["content"]
        assert "目标岗位：后端｜投递类型：校招" in messages[3]["content"]
        assert messages[3]["content"].count("## 总体判断") == 1
        assert ("报告未完成" in messages[3]["content"]) is incomplete
        streamed = "".join(event.removeprefix("data: ").removesuffix("\n\n").replace("\\n", "\n")
                           for event in events if not event.startswith("data: [STATE]") and "[DONE]" not in event)
        assert streamed == messages[3]["content"]
        assert sum(stage == "synthesis" for stage, _ in model.calls) == 1
    finally:
        conn.close()
