"""真实模型的结构与证据回查；需要显式 -m llm，会产生费用。"""

import pytest

from agent.schema import MODULE_LABELS

pytestmark = pytest.mark.llm


def test_report_has_all_guidance_sections(report):
    assert {section.module for section in report.sections} == set(MODULE_LABELS)
    assert not report.context.missing_fields()


def test_evidence_is_from_resume(report, golden_resume):
    text = "".join(golden_resume.resume_text.split())
    for item in [*report.sections, *report.actions, *report.interview_questions]:
        for quote in item.evidence:
            assert "".join(quote.split()) in text


def test_report_contains_no_score_protocol(report):
    data = report.model_dump()
    assert "overall_score" not in data and "dimensions" not in data
    assert report.actions
