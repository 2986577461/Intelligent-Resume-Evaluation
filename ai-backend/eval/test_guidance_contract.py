"""A 修改建议 / B 关联追问的工程协议回归；假模型不证明语义正确。"""

import copy

import pytest
from pydantic import ValidationError

from agent.graph import build_eval_graph
from agent.nodes import synthesis_node
from agent.report_stream import render_report
from agent.resume_context import normalize_text
from agent.schema import Report
from eval.test_offline_pipeline import CONTEXT, DATA, EXTRACTION, SYNTHESIS, ScriptedModel, run


def report_data():
    return copy.deepcopy({"context": CONTEXT, **SYNTHESIS})


def test_no_forced_criticism_or_questions():
    data = {"overview": "现有描述已清楚，本次没有需要优先修改的问题。", "actions": [], "interview_questions": []}
    report, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": [data]}))
    assert "未识别出需要优先修改的问题" in report
    assert "面试官可能继续追问" not in report
    assert "".join(chunks) == report
    assert metrics[-1]["outcome"] == "success"


def test_clear_content_can_have_a_question_without_an_action():
    data = copy.deepcopy(SYNTHESIS)
    data["actions"] = []
    data["interview_questions"][0]["action_id"] = None
    report, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": [data]}))
    assert "未识别出需要优先修改的问题" in report
    assert "考察点" in report and "准备方向" in report
    assert "关联 A1" not in report and "".join(chunks) == report
    assert metrics[-1]["outcome"] == "success"


@pytest.mark.parametrize("field", ["subject", "observation", "impact", "action"])
def test_action_requires_specific_explanation_fields(field):
    data = report_data()
    data["actions"][0][field] = " "
    with pytest.raises(ValidationError):
        Report.model_validate(data)


@pytest.mark.parametrize("field", ["subject", "question", "purpose", "preparation"])
def test_question_requires_purpose_and_preparation(field):
    data = report_data()
    del data["interview_questions"][0][field]
    with pytest.raises(ValidationError):
        Report.model_validate(data)


def test_question_requires_resume_evidence():
    data = report_data()
    data["interview_questions"][0]["evidence"] = []
    with pytest.raises(ValidationError):
        Report.model_validate(data)


@pytest.mark.parametrize("field,count", [("actions", 4), ("interview_questions", 6)])
def test_report_rejects_excess_items(field, count):
    data = report_data()
    data[field] = data[field] * count
    with pytest.raises(ValidationError):
        Report.model_validate(data)


def test_duplicate_action_ids_rejected_by_schema_and_stream():
    data = copy.deepcopy(SYNTHESIS)
    data["actions"] *= 2
    with pytest.raises(ValidationError):
        Report.model_validate({"context": CONTEXT, **data})
    report, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": [data]}))
    assert "报告未完成" in report and "".join(chunks) == report
    # 新契约：第一条校验通过就发布；后续重复 ID 不可撤回前文，但绝不能再发布一遍。
    assert report.count("### A1") == 1
    assert metrics[-1]["outcome"] == "incomplete"


def test_dangling_question_is_not_published():
    data = copy.deepcopy(SYNTHESIS)
    data["interview_questions"][0]["action_id"] = "A3"
    report, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": [data]}))
    assert "报告未完成" in report and "".join(chunks) == report
    assert data["interview_questions"][0]["question"] not in report
    assert metrics[-1]["outcome"] == "incomplete"


def test_invented_question_evidence_is_not_published():
    data = copy.deepcopy(SYNTHESIS)
    data["interview_questions"][0]["evidence"] = ["我设计了千万用户的缓存平台"]
    report, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": [data]}))
    assert "报告未完成" in report and "".join(chunks) == report
    assert "千万用户" not in report
    assert metrics[-1]["outcome"] == "incomplete"


def test_missing_information_action_is_rendered_as_unconfirmed():
    data = report_data()
    data["actions"][0].update(evidence=[], observation="未提供个人职责，无法确认贡献归属。",
                              kind="信息确认", action="确认哪些工作由自己完成，不将团队成果据为己有。")
    text = render_report(Report.model_validate(data))
    assert "未提供对应信息" in text and "不能据此断言" in text
    assert "信息确认" in text


def test_actions_are_sorted_without_breaking_stable_question_references():
    data = copy.deepcopy(SYNTHESIS)
    low = {**data["actions"][0], "id": "A2", "priority": "低", "subject": "次要表述"}
    data["actions"].insert(0, low)
    text, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": [data]}))
    assert text.index("### A1") < text.index("### A2")
    assert "关联 A1" in text
    assert text == render_report(Report(context=CONTEXT, **data)) == "".join(chunks)
    assert metrics[-1]["outcome"] == "success"


def test_nonstream_analysis_and_stream_use_same_protocol():
    state = {**EXTRACTION, "resume_text": DATA["text"], "context": CONTEXT, "page_count": 2}
    model = ScriptedModel()
    result = synthesis_node(state, model)
    assert result["synthesis"] == SYNTHESIS
    assert [stage for stage, _ in model.calls] == ["synthesis"]
    assert "reviews" not in model.calls[0][1]
    assert model.calls[0][1]["resume_text"] == normalize_text(DATA["text"])
    graph = build_eval_graph(ScriptedModel())
    output = graph.invoke({"resume_text": DATA["text"]})
    assert output["report"] == render_report(Report(context=CONTEXT, **SYNTHESIS))
