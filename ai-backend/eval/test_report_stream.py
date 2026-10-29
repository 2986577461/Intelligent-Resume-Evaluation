"""增量 JSON、正文首段时机、中途失败及非流式一致性。全部假模型离线执行。"""

import copy
import json

import pytest
from langchain_core.messages import AIMessageChunk

from agent.report_stream import ReportJSONStream, render_report
from agent.schema import Report
from eval.test_offline_pipeline import CONTEXT, SYNTHESIS, ScriptedModel, run, signal


@pytest.mark.parametrize("size", [1, 2, 7, 91, 10000])
def test_parser_handles_arbitrary_boundaries_and_escaped_content(size):
    data = copy.deepcopy(SYNTHESIS)
    data["overview"] = 'JSON 字符："引号"\\路径\n换行，中文😀，字符串里的 } ] , 不应被当作边界'
    text = json.dumps(data, ensure_ascii=True)
    parser, events = ReportJSONStream(), []
    for index in range(0, len(text), size):
        events.extend(parser.feed(text[index:index + size]))
    assert parser.finish() == data
    assert "".join(value for key, kind, value in events if key == "overview" and kind == "delta") == data["overview"]
    assert ("overview", None, data["overview"]) in events
    assert len([event for event in events if event[0] == "actions" and isinstance(event[1], int)]) == 1


@pytest.mark.parametrize("text", [
    '{"overview":"a","overview":"b"}',
    '{"unexpected":"value"}',
    '{"actions":[{"id":"A1","id":"A2"}]}',
    '{"overview":"a"} trailing',
    '{"overview":"unfinished',
    '{"actions":[{"id":"A1"}',
    '{"overview":"a",}',
])
def test_parser_rejects_malformed_duplicate_or_incomplete_json(text):
    parser = ReportJSONStream()
    with pytest.raises(ValueError):
        list(parser.feed(text))
        parser.finish()


@pytest.mark.parametrize("jd,provided", [
    (None, False),
    (signal("Python 检索服务", "招聘要求：Python 检索服务", "user"), True),
    (signal("Python 检索服务", "Python", inferred=True), False),
])
def test_report_explains_analysis_basis_without_requiring_jd(jd, provided):
    report = render_report(Report(context={**CONTEXT, "job_description": jd}, **SYNTHESIS))
    assert ("本次结合目标岗位与您提供的招聘要求（JD）分析" in report) == provided
    assert ("本次按目标岗位分析；如提供具体招聘要求（JD），可进一步对照" in report) != provided
    assert "未提供具体 JD" not in report
    assert "## 优先修改清单" in report


def test_streamed_text_matches_deterministic_non_stream_rendering():
    model = ScriptedModel()
    report, chunks, _, metrics = run(model)
    expected = render_report(Report(context=CONTEXT, **SYNTHESIS))
    assert len(chunks) >= 5 and "".join(chunks) == report == expected
    assert metrics[-1]["outcome"] == "success"
    assert report.endswith("不代表实际能力或录用结果。\n")


def test_missing_required_field_after_first_content_marks_incomplete_without_retry():
    bad = copy.deepcopy(SYNTHESIS)
    del bad["interview_questions"]
    model = ScriptedModel(faults={"synthesis": [bad]})
    report, chunks, _, metrics = run(model)
    assert "总体判断" in report and "报告未完成" in report
    assert "".join(chunks) == report
    assert sum(stage == "synthesis" for stage, _ in model.calls) == 1
    assert metrics[-1]["outcome"] == "incomplete"
    assert "不代表实际能力或录用结果" not in report


def test_network_failure_after_first_content_does_not_replay_or_leak_exception():
    class BrokenModel(ScriptedModel):
        def stream(self, messages, config=None):
            response = self.invoke(messages, config)
            # 首段 JSON 已完整，后面数组尚未生成，随后模拟连接中断。
            end = response.content.index('"actions"')
            yield AIMessageChunk(content=response.content[:end])
            raise ConnectionError("PRIVATE SERVICE ERROR")

    model = BrokenModel()
    report, chunks, _, metrics = run(model)
    assert "总体判断" in report and "报告未完成" in report
    assert "PRIVATE" not in report and report.count("总体判断") == 1
    assert "".join(chunks) == report
    assert sum(stage == "synthesis" for stage, _ in model.calls) == 1
    assert metrics[-1]["outcome"] == "incomplete"


def test_validation_failure_before_first_content_can_retry_without_leaking_json():
    bad = {**SYNTHESIS, "overview": ""}
    model = ScriptedModel(faults={"synthesis": [bad]})
    report, chunks, _, metrics = run(model)
    assert "报告未完成" not in report and "非法状态" not in report
    assert "".join(chunks) == report
    assert sum(stage == "synthesis" for stage, _ in model.calls) == 2
    assert metrics[-1]["outcome"] == "success"


def test_out_of_order_model_fields_are_buffered_until_display_order_is_ready():
    data = copy.deepcopy(SYNTHESIS)
    data = {"interview_questions": data["interview_questions"],
            "actions": data["actions"], "overview": data["overview"]}
    report, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": [data]}))
    assert report == render_report(Report(context=CONTEXT, **SYNTHESIS))
    assert "".join(chunks) == report and metrics[-1]["outcome"] == "success"


def test_no_questions_is_a_complete_report_not_an_interruption():
    data = {**SYNTHESIS, "interview_questions": []}
    report, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": [data]}))
    assert "面试官可能继续追问" not in report and "报告未完成" not in report
    assert "".join(chunks) == report and metrics[-1]["outcome"] == "success"
