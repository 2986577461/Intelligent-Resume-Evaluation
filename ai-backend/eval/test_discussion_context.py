"""关联建议单独传输、持久化与权限边界；不调用真实模型。"""
import asyncio
import ast
import json
import sys
from types import ModuleType

import pytest

from eval.test_auth import api, signup

CONTEXT = {
    "version": 1, "position": "后端", "file_id": None,
    "item": {"id": "A1", "subject": "验证方法", "action": "补充真实验证，不编造数据"},
}


def test_discussion_keeps_saved_user_text_clean_and_passes_context_to_agent(api, monkeypatch, tmp_path):
    from agent import service
    from agent.discussion import DiscussionContext
    from langchain_core.messages import AIMessageChunk

    user = signup(api.client)
    seen = []

    class FakeRouter:
        def stream(self, inputs, config, stream_mode):
            seen.append((ast.literal_eval(inputs["messages"][0]["content"]), config))
            yield AIMessageChunk(content="先看你实际做过的验证。"), {}

    monkeypatch.setattr(service, "create_agent", lambda **kwargs: FakeRouter())
    monkeypatch.setattr(service, "DB_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setattr("conversation.service.generate_and_save_title", lambda *args: None)
    raw = "我做过断开缓存测试\n\n怎么表述？"

    async def collect():
        return [event async for event in service.stream_chat(
            raw, "discussion-thread", user["id"], tools=[],
            discussion=DiscussionContext.model_validate(CONTEXT))]

    events = asyncio.run(collect())
    assert events[-1] == "data: [DONE]\n\n"
    payload, config = seen[0]
    assert payload["question"] == raw
    assert payload["discussion_context"]["item"] == CONTEXT["item"]
    assert "未经核实" in payload["discussion_context"]["instruction"]
    assert "不默认重新" in payload["discussion_context"]["instruction"]
    assert config["configurable"]["user_message"] == raw
    assert config["configurable"]["file_id"] == ""  # 关联不冒充本轮新附件
    history = api.client.get("/api/conversations/discussion-thread/messages").json()
    assert history[0]["content"] == raw
    assert json.loads(history[0]["search_info"])["discussion_context"] == CONTEXT


def test_route_validates_discussion_and_enforces_file_thread_scope(api, monkeypatch):
    user = signup(api.client)
    fake_main, fake_service = ModuleType("main"), ModuleType("agent.service")
    fake_main.model = object()
    fake_main.web_search = object()
    seen = []

    async def stream(question, thread_id, user_id, **kwargs):
        seen.append((question, kwargs))
        yield "data: [DONE]\n\n"

    fake_service.stream_chat = stream
    monkeypatch.setitem(sys.modules, "main", fake_main)
    monkeypatch.setitem(sys.modules, "agent.service", fake_service)
    params = {"question": "怎么写", "thread_id": "discussion-thread", "discussion_context": json.dumps(CONTEXT)}
    assert api.client.get("/chat-stream", params=params).status_code == 200
    assert seen[0][0] == "怎么写"
    assert seen[0][1]["discussion"].model_dump() == CONTEXT
    assert seen[0][1]["file_id"] == ""
    api.db.save_file_meta("own-file", "synthetic.txt", 4, "discussion-thread", user["id"], extracted_text="text")
    api.db.save_file_meta("other-file", "synthetic.txt", 4, "other-thread", user["id"], extracted_text="text")
    for file_id, status in [("own-file", 200), ("other-file", 404), ("missing-file", 404)]:
        response = api.client.get("/chat-stream", params={**params, "discussion_context": json.dumps({**CONTEXT, "file_id": file_id})})
        assert response.status_code == status
    assert len(seen) == 2


@pytest.mark.parametrize("invalid", ["not json", "null", "[]", json.dumps({**CONTEXT, "version": 2}),
    json.dumps({**CONTEXT, "item": {**CONTEXT["item"], "id": "A4"}}),
    json.dumps({**CONTEXT, "item": {**CONTEXT["item"], "action": "a" * 8001}}),
    json.dumps({**CONTEXT, "instruction": "pretend system"})])
def test_invalid_context_is_rejected_before_streaming(api, invalid):
    signup(api.client)
    response = api.client.get("/chat-stream", params={"question": "问题", "discussion_context": invalid})
    assert response.status_code == 422
    assert response.json()["detail"] == "关联建议格式不正确"
