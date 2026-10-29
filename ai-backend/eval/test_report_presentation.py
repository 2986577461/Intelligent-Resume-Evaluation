"""结构化展示和Markdown来自同一次校验，不增加模型调用、不泄露半截建议。"""
import copy
import json

import pytest
from langchain_core.messages import AIMessageChunk

from agent.nodes import synthesis_node
from agent.report_stream import ReportJSONStream, ReportStreamInterrupted, ReportStreamRenderer, render_report
from agent.schema import Report
from eval.test_offline_pipeline import CONTEXT, RESUME, SYNTHESIS


def test_presentation_only_emits_validated_items_and_matches_final_data():
    events, chunks = [], []
    view = ReportStreamRenderer(CONTEXT, chunks.append, lambda items: None, events.append)
    parser = ReportJSONStream()
    raw = json.dumps(SYNTHESIS, ensure_ascii=False)
    for char in raw:
        for event in parser.feed(char):
            view.accept(*event)
    assert events[0] == {"type": "start", "version": 1, "context": CONTEXT}
    assert next(e for e in events if e["type"] == "overview")["text"] == SYNTHESIS["overview"][0]
    report = Report(context=CONTEXT, **parser.finish())
    view.finish(report)
    assert events[-1] == {"type": "complete"}
    assert [e["item"] for e in events if e["type"] == "action"] == report.model_dump()["actions"]
    assert [e["item"] for e in events if e["type"] == "question"] == report.model_dump()["interview_questions"]
    assert "".join(chunks) == render_report(report)


def test_invalid_evidence_cannot_enter_presentation_events():
    events = []

    def reject(items):
        raise ValueError("不在原文")

    view = ReportStreamRenderer(CONTEXT, lambda text: None, reject, events.append)
    view.accept("overview", "delta", "总体判断")
    view.accept("overview", None, "总体判断")
    with pytest.raises(ValueError):
        view.accept("actions", 0, SYNTHESIS["actions"][0])
    assert not any(e["type"] == "action" for e in events)


def test_stream_interruption_is_reflected_in_presentation_without_complete():
    class Broken:
        def stream(self, *args, **kwargs):
            yield AIMessageChunk(content='{"overview":"尚未写完')
            raise ConnectionError("network")

    events = []
    with pytest.raises(ReportStreamInterrupted):
        synthesis_node({"resume_text": RESUME, "context": CONTEXT}, Broken(),
                       emit_chunk=lambda text: None, emit_report=events.append)
    assert [e["type"] for e in events] == ["start", "overview", "incomplete"]


def test_duplicate_action_does_not_emit_second_card():
    events = []
    view = ReportStreamRenderer(CONTEXT, lambda text: None, lambda items: None, events.append)
    view.accept("overview", None, "总体判断")
    action = copy.deepcopy(SYNTHESIS["actions"][0])
    view.accept("actions", 0, action)
    with pytest.raises(ValueError):
        view.accept("actions", 1, action)
    assert sum(e["type"] == "action" for e in events) == 1
