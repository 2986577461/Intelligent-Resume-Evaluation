"""重复真实调用的有限证据稳定性抽查，不替代人工语义审阅；会计费。"""

import pytest

from eval.helpers import run_eval_pipeline

pytestmark = pytest.mark.consistency


def test_repeated_reports_share_an_evidence_anchor(eval_graph, golden_resume):
    first = run_eval_pipeline(eval_graph, golden_resume.resume_text, golden_resume.position, "校招")
    second = run_eval_pipeline(eval_graph, golden_resume.resume_text, golden_resume.position, "校招")

    def evidence(report):
        return {"".join(quote.split()) for item in [*report.actions, *report.interview_questions]
                for quote in item.evidence}

    first_evidence, second_evidence = evidence(first), evidence(second)
    if not first_evidence or not second_evidence:
        pytest.skip("报告无引用锚点，需要人工审核空建议是否合理，不能视为稳定性通过")
    assert any(a in b or b in a for a in first_evidence for b in second_evidence), "重复运行没有共同引用锚点"
