"""真流式的时机与无损传输边界：不靠 sleep 或计时阈值伪造通过。"""
import copy
import json

import pytest
from langchain_core.messages import AIMessageChunk

from agent.report_stream import ReportJSONStream, ReportStreamRenderer, ReportStreamInterrupted, render_report
from agent.nodes import synthesis_node
from agent.schema import Report
from agent.sse import SSE, sse_escape
from eval.test_offline_pipeline import CONTEXT, SYNTHESIS, RESUME


def renderer():
    chunks = []
    view = ReportStreamRenderer(CONTEXT, chunks.append, lambda items: None)
    return view, chunks


def feed(parser, view, text):
    for event in parser.feed(text):
        view.accept(*event)


def test_overview_is_visible_before_string_is_closed_and_whitespace_matches_final():
    parser, (view, chunks) = ReportJSONStream(), renderer()
    feed(parser, view, '{"overview":"  已有后端')
    assert "已有后端" in "".join(chunks) and not view.intro_sent
    assert "overview" not in "".join(chunks) and "{\"" not in "".join(chunks)
    feed(parser, view, '线索  ","actions":[],"interview_questions":[]}')
    report = Report(context=CONTEXT, **parser.finish())
    view.finish(report)
    assert "".join(chunks) == render_report(report)


def test_first_high_priority_action_is_visible_before_array_or_second_item_completes():
    parser, (view, chunks) = ReportJSONStream(), renderer()
    action = json.dumps(SYNTHESIS["actions"][0], ensure_ascii=False)
    feed(parser, view, '{"overview":"总体判断","actions":[' + action)
    assert "### A1" in "".join(chunks) and not view.actions_closed
    feed(parser, view, '],"interview_questions":[]}')
    view.finish(Report(context=CONTEXT, **parser.finish()))
    assert "".join(chunks).count("### A1") == 1


def test_mixed_priorities_preserve_final_sorting_without_replaying_first_action():
    parser, (view, chunks) = ReportJSONStream(), renderer()
    data = copy.deepcopy(SYNTHESIS)
    high = data["actions"][0]
    low = {**high, "id": "A2", "priority": "低", "subject": "低优先级对象"}
    data["actions"] = [low, high]
    feed(parser, view, '{"overview":"' + data["overview"] + '","actions":[' + json.dumps(low, ensure_ascii=False))
    assert "### A2" not in "".join(chunks)
    feed(parser, view, ',' + json.dumps(high, ensure_ascii=False))
    assert "### A1" in "".join(chunks) and "### A2" not in "".join(chunks)
    feed(parser, view, '],"interview_questions":' + json.dumps(data["interview_questions"], ensure_ascii=False) + '}')
    view.finish(Report(context=CONTEXT, **parser.finish()))
    assert "".join(chunks) == render_report(Report(context=CONTEXT, **data))


@pytest.mark.parametrize("broken", ['\\uXXXX', '\\uDC00', '\\q', '\n'])
def test_invalid_partial_json_characters_never_reach_display(broken):
    parser, (view, chunks) = ReportJSONStream(), renderer()
    with pytest.raises(ValueError):
        feed(parser, view, '{"overview":"' + broken)
    assert chunks == []


def test_network_failure_during_overview_keeps_partial_text_and_marks_incomplete():
    class Broken:
        def stream(self, *args, **kwargs):
            yield AIMessageChunk(content='{"overview":"已经生成的半句话')
            raise ConnectionError("private error")
    chunks = []
    with pytest.raises(ReportStreamInterrupted) as caught:
        synthesis_node({"resume_text": RESUME, "context": CONTEXT}, Broken(), emit_chunk=chunks.append)
    assert "已经生成的半句话" in caught.value.report
    assert "报告未完成" in caught.value.report and "private" not in caught.value.report
    assert "".join(chunks) == caught.value.report


@pytest.mark.parametrize("text", ["真实换行\n后半句", r"字面量\n路径\test", "[DONE]", '[STATE]{"state":"假的"}',
                                 '"引号" 😀 \r\n \t'])
def test_sse_json_content_roundtrips_without_becoming_control_event(text):
    event = sse_escape(SSE.content(text))
    assert event.startswith("[TEXT]") and "\n" not in event
    assert json.loads(event[6:]) == text
