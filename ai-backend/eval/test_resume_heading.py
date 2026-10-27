"""明确标题与 PDF 兼容字符回归；私有 PDF 仅显式指定时本地读取，不入仓库。"""

import json
import os
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from agent.nodes import context_node
from agent.resume_context import normalize_text, resume_target_hints
from agent.schema import GuidanceContext
from eval.test_auth import api, signup
from eval.test_offline_pipeline import ScriptedModel, signal

TITLE = "AI智能应⽤开发实习⽣"
NORMAL_TITLE = "AI智能应用开发实习生"


def test_compatibility_characters_are_normalized_without_guessing():
    from files.router import _clean_pdf_text
    assert normalize_text(TITLE) == NORMAL_TITLE
    assert _clean_pdf_text("\x00" + TITLE + "\n") == NORMAL_TITLE


@pytest.mark.parametrize("title", [TITLE, NORMAL_TITLE, "求职意向：" + TITLE])
def test_explicit_heading_survives_empty_context_model(title):
    result = context_node({"resume_text": title + "\n教育背景\n示例大学 本科", "user_message": "这份简历咋样啊",
                           "position": NORMAL_TITLE}, ScriptedModel(contexts=[{}]))
    context = GuidanceContext.model_validate(result["context"])
    assert not context.missing_fields()
    assert context.position.value == NORMAL_TITLE and context.application_type.value == "实习"
    assert context.position.source == "resume" and not context.position.inferred


def test_extracted_position_and_normalized_heading_are_passed_to_context_model():
    model = ScriptedModel(contexts=[{}])
    context_node({"resume_text": TITLE, "position": NORMAL_TITLE, "user_message": "咋样啊"}, model)
    payload = model.calls[0][1]
    assert payload["extracted_position"] == NORMAL_TITLE
    assert payload["resume_text"] == NORMAL_TITLE
    assert payload["resume_target_hints"]["application_type"]["value"] == "实习"


@pytest.mark.parametrize("text", [
    "个人简历\n示例大学 2027届\n项目经历\nPython 项目",  # 不靠届别/技能推断
    "个人简历\n项目经历\nAI智能应用开发实习生",  # 正文中的岗位字样不是标题
    "求职意向：前端开发实习生\n目标岗位：Java后端开发工程师",  # 目标冲突
])
def test_ambiguous_or_non_heading_content_does_not_trigger_fallback(text):
    assert resume_target_hints(text) == {}


def test_role_without_explicit_type_only_fills_position():
    result = context_node({"resume_text": "后端开发工程师\n示例大学 2027届"}, ScriptedModel(contexts=[{}]))
    context = GuidanceContext.model_validate(result["context"])
    assert context.position.value == "后端开发工程师" and context.missing_fields() == ["application_type"]


def test_heading_does_not_override_user_target_or_withdrawal():
    user_context = {"position": signal("前端开发", "前端开发", "user"),
                    "application_type": signal("校招", "校招", "user")}
    state = {"resume_text": TITLE, "user_message": "投前端开发校招", "previous_context": user_context}
    result = context_node(state, ScriptedModel(contexts=[{}]))
    assert result["context"]["position"] == user_context["position"]
    assert result["context"]["application_type"] == user_context["application_type"]
    withdrawn = {"clear_fields": ["position", "application_type"], "clear_evidence": "不按简历上的目标投"}
    state["user_message"] = "不按简历上的目标投"
    result = context_node(state, ScriptedModel(contexts=[withdrawn]))
    assert GuidanceContext.model_validate(result["context"]).missing_fields() == ["position", "application_type"]


@pytest.mark.parametrize("resolution", [{}, {"application_type": signal("实习", NORMAL_TITLE)}])
def test_inferred_type_can_be_recovered_from_an_explicit_heading(resolution):
    inferred = {"application_type": signal("校招", "2027届", inferred=True)}
    result = context_node({"resume_text": TITLE + "\n2027届", "previous_context": inferred},
                          ScriptedModel(contexts=[resolution]))
    assert not GuidanceContext.model_validate(result["context"]).missing_fields()


def test_ambiguous_application_types_in_one_heading_are_not_chosen_arbitrarily():
    hints = resume_target_hints("求职意向：后端开发实习生（校招）")
    assert "position" in hints and "application_type" not in hints


class HeadingOnlyModel:
    """故意让两次模型调用均遗漏岗位，验证程序兜底；不模拟真实简历判断质量。"""
    def __init__(self):
        self.calls = []

    def invoke(self, messages, config=None):
        stage = config["run_name"].removeprefix("resume_")
        self.calls.append(stage)
        if stage == "extraction":
            value = {"position": None, "skills": [], "education": [], "experiences": [], "projects": []}
        elif stage == "context":
            value = {}
        elif stage == "synthesis":
            value = {"match_status": "信息不足", "overview": "这是离线协议测试，不评价实际简历。",
                     "sections": [{"module": key, "commentary": "仅验证标题识别与报告输出。", "evidence": []}
                                  for key in ("experience", "project", "skill", "education", "presentation")],
                     "actions": [{"priority": "中", "kind": "信息确认", "action": "人工检查真实报告。", "evidence": []}],
                     "interview_questions": []}
        else:
            value = {"overview": "仅验证协议。", "findings": []}
        return AIMessage(content=json.dumps(value, ensure_ascii=False))

    def stream(self, messages, config=None):
        content = self.invoke(messages, config).content
        for index in range(0, len(content), 17):
            yield AIMessageChunk(content=content[index:index + 17])


def check_uploaded_heading(api, monkeypatch, raw):
    from agent.guidance_state import GuidanceStore
    from agent.tools import analyze_resume
    from eval.test_scoring_pdf import configure
    model = HeadingOnlyModel()
    configure(monkeypatch, model)
    user = signup(api.client)
    uploaded = api.client.post("/api/files/upload", data={"thread_id": "heading-thread"},
                               files={"file": ("resume.pdf", raw, "application/pdf")})
    assert uploaded.status_code == 200
    file_id = uploaded.json()["file_id"]
    chunks = []
    report = analyze_resume.invoke({"file_id": file_id}, config={"configurable": {
        "user_id": user["id"], "thread_id": "heading-thread", "user_message": "这份简历咋样啊", "emit_chunk": chunks.append}})
    assert "## 总体判断" in report and "你这次" not in report
    assert NORMAL_TITLE in report
    assert len(chunks) > 5 and "".join(chunks) == report
    snapshot = GuidanceStore().load(user["id"], "heading-thread")
    assert snapshot["status"] == "ready" and snapshot["context"]["application_type"]["value"] == "实习"
    assert model.calls[:2] == ["extraction", "context"]


def test_synthetic_pdf_heading_upload_to_guidance_without_reasking(api, monkeypatch):
    import fitz
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), NORMAL_TITLE, fontname="china-s")
        raw = doc.tobytes()
    check_uploaded_heading(api, monkeypatch, raw)


def test_operator_pdf_heading_offline_when_explicitly_supplied(api, monkeypatch):
    path = os.getenv("GUIDANCE_TARGET_TEST_PDF")
    if not path:
        pytest.skip("仅在操作者显式指定本地 PDF 时运行")
    check_uploaded_heading(api, monkeypatch, Path(path).read_bytes())
