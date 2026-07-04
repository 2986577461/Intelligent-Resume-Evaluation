"""离线结构测试：用 FakeListChatModel 灌入预设 JSON，验证图接线和 JSON 解析健壮性。
不调用真实 LLM API，默认跟随裸 `pytest` 一起跑。
"""

import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent.graph import build_eval_graph
from agent.nodes import _call_llm

RESUME_EXTRACT_JSON = json.dumps({
    "position": "后端开发工程师",
    "skills": ["熟悉Python", "了解Redis"],
    "education": [{"school": "示例大学", "greade": "一本本科", "major": "计算机科学与技术", "时间": "2020年9月-2024年6月"}],
    "experiences": [],
    "projects": [{"时间": "2023年1月-2023年6月", "项目名称": "示例项目", "使用的工具": ["Python"],
                  "项目描述": "示例描述", "工作内容": ["示例工作"], "解决的问题": ["示例问题"]}],
}, ensure_ascii=False)

ANALYST_JSON = json.dumps({
    "score": 8,
    "detail": ["示例评分理由"],
    "evidence": ["示例原文"],
}, ensure_ascii=False)

REPORT_JSON = json.dumps({
    "overall_score": 60,
    "dimensions": {
        "skill_match": {"score": 8, "detail": ["示例理由"]},
        "project_depth": {"score": 25, "detail": ["示例理由"]},
        "experience": {"score": 12, "detail": ["示例理由"]},
        "education": {"score": 15, "detail": ["示例理由"]},
    },
    "strengths": ["示例亮点"],
    "risks": ["示例风险"],
    "interview_questions": ["请介绍一下示例项目"],
    "summary": "示例总结",
}, ensure_ascii=False)


def test_graph_wiring_produces_valid_report():
    """四个并行 analyst 的返回 shape 完全一致，与并行执行顺序无关"""
    fake_model = FakeListChatModel(responses=[
        RESUME_EXTRACT_JSON, ANALYST_JSON, ANALYST_JSON, ANALYST_JSON, ANALYST_JSON, REPORT_JSON,
    ])
    graph = build_eval_graph(fake_model)

    report_text = None
    for step in graph.stream({"resume_text": "示例简历文本"}):
        node = step.get("report")
        if node:
            report_text = node.get("report") if isinstance(node, dict) else node

    assert report_text is not None
    parsed = json.loads(report_text)
    assert set(parsed["dimensions"].keys()) == {"skill_match", "project_depth", "experience", "education"}
    assert parsed["overall_score"] == 60


def test_call_llm_parses_json_code_fence():
    fake_model = FakeListChatModel(responses=[f"```json\n{ANALYST_JSON}\n```"])
    result = _call_llm("system prompt", "user text", fake_model)
    assert result["score"] == 8
    assert result["detail"] == ["示例评分理由"]


def test_call_llm_parses_plain_code_fence():
    fake_model = FakeListChatModel(responses=[f"```\n{ANALYST_JSON}\n```"])
    result = _call_llm("system prompt", "user text", fake_model)
    assert result["score"] == 8


def test_call_llm_falls_back_on_invalid_json():
    fake_model = FakeListChatModel(responses=["这不是合法的 JSON"])
    result = _call_llm("system prompt", "user text", fake_model)
    assert result == {"raw": "这不是合法的 JSON"}
