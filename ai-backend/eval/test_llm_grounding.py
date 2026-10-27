"""真实模型的实体引用抽查，不将命中一个名词等同于语义正确。显式执行会计费。"""

import pytest

from eval.helpers import flatten_report_text

pytestmark = pytest.mark.llm


def test_must_mention_entities_are_grounded(report, golden_resume):
    if not golden_resume.must_mention:
        pytest.skip("样本未标注实体")
    text = flatten_report_text(report)
    assert any(term in text for term in golden_resume.must_mention)


def test_interview_questions_reference_specifics(report, golden_resume):
    if not golden_resume.must_mention or not report.interview_questions:
        pytest.skip("样本没有可检查的实体或追问")
    assert any(term in item.question for item in report.interview_questions for term in golden_resume.must_mention)
