"""真实模型的结构与证据回查；需要显式 -m llm，会产生费用。"""

import pytest

from agent.nodes import _contains

pytestmark = pytest.mark.llm


def test_report_has_bounded_actions_and_linked_questions(report):
    assert len(report.actions) <= 3 and len(report.interview_questions) <= 5
    ids = {item.id for item in report.actions}
    assert all(item.action_id is None or item.action_id in ids for item in report.interview_questions)
    assert not report.context.missing_fields()


def test_evidence_is_from_resume(report, golden_resume):
    for item in [*report.actions, *report.interview_questions]:
        for quote in item.evidence:
            assert _contains(golden_resume.resume_text, quote)


def test_report_contains_no_score_protocol(report):
    data = report.model_dump()
    assert "overall_score" not in data and "dimensions" not in data
    assert "match_status" not in data and "sections" not in data
