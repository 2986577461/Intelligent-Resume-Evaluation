"""显式真实调用：合成文本 + 临时 SQLite，真实路由/分析/服务 SSE，不访问私有文件。

LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false python -m pytest \
  eval/test_llm_streaming.py -m llm -v
可用 GUIDANCE_STREAM_RESULT=/tmp/...json 保存本次链路证据（仅合成数据）。
"""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from eval.test_auth import api, signup
from eval.run_guidance_acceptance import load_cases

pytestmark = pytest.mark.llm


def test_real_route_tool_sse_and_saved_history(api, monkeypatch, tmp_path, real_model):
    from langchain.chat_models import init_chat_model
    from agent import service
    from agent.tools import analyze_resume, list_resumes, parse_resume_by_index

    case = next(case for case in load_cases() if case["id"] == "S3")
    user = signup(api.client)["id"]
    thread = "synthetic-stream-thread"
    file_id = "synthetic-file"
    api.db.ensure_conversation_and_save_user(thread, "以下是合成测试数据", user)
    api.db.save_file_meta(file_id, "synthetic.txt", len(case["resume_text"]), thread, user,
                         extracted_text=case["resume_text"])
    monkeypatch.setattr(service, "DB_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setattr("conversation.service.generate_and_save_title", lambda *args: None)
    monkeypatch.setattr("state.manager.get_cache_manager", lambda: None)
    progress = {"stream_started": False, "stream_finished": False, "source_chunks": 0}

    class ObservedModel:
        def invoke(self, *args, **kwargs):
            return real_model.invoke(*args, **kwargs)

        def stream(self, *args, **kwargs):
            progress["stream_started"] = True
            for chunk in real_model.stream(*args, **kwargs):
                progress["source_chunks"] += 1
                yield chunk
            progress["stream_finished"] = True

    monkeypatch.setattr("agent.scoring.create_scoring_model", lambda: ObservedModel())
    router = init_chat_model(model="deepseek-v4-flash", max_tokens=4096, timeout=60, max_retries=0)
    started = time.perf_counter()
    events = []

    async def collect():
        async for event in service.stream_chat(
            case["user_message"], thread, user, model=router,
            tools=[analyze_resume, list_resumes, parse_resume_by_index], file_id=file_id,
        ):
            events.append({"elapsed": round(time.perf_counter() - started, 4), "event": event,
                           **progress})

    asyncio.run(collect())
    text_events = [item for item in events if item["event"].startswith("data: [TEXT]")]
    text = "".join(json.loads(item["event"].removeprefix("data: [TEXT]").removesuffix("\n\n"))
                   for item in text_events)
    conn = api.db.get_app_db()
    try:
        saved = conn.execute("SELECT content FROM messages WHERE role='ai' ORDER BY id DESC LIMIT 1").fetchone()
        saved_text = saved["content"] if saved else None
    finally:
        conn.close()
    evidence = {"sample": case["id"], "sample_type": "synthetic", "events": events,
                "text": text, "saved_text": saved_text, "elapsed": round(time.perf_counter() - started, 4),
                "titles_disabled": True, "redis_disabled": True}
    if path := os.getenv("GUIDANCE_STREAM_RESULT"):
        Path(path).write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    assert events[-1]["event"] == "data: [DONE]\n\n"
    assert text == saved_text
    assert progress["stream_started"] and progress["stream_finished"]
    assert any(not item["stream_finished"] and item["stream_started"] for item in text_events), (
        "模型生成结束前 SSE 消费端未收到正文")
    assert len(text_events) > 5
    assert text.count("## 总体判断") == 1 and text.count("## 优先修改清单") == 1
    assert "报告未完成" not in text and "生成失败" not in text
    assert '"overview"' not in text and '"interview_questions"' not in text
