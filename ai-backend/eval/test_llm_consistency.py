"""真实调用 LLM 评测：同一份简历跑两次，验证总分波动在容差内。
费用是 schema/grounding 测试的两倍，默认不跑，用 `pytest -m consistency` 显式执行。
"""

import pytest

from eval.helpers import run_eval_pipeline

pytestmark = pytest.mark.consistency

SCORE_VARIANCE_TOLERANCE = 15


def test_overall_score_is_stable_across_runs(eval_graph, golden_resume):
    first = run_eval_pipeline(eval_graph, golden_resume.resume_text)
    second = run_eval_pipeline(eval_graph, golden_resume.resume_text)

    diff = abs(first.overall_score - second.overall_score)
    assert diff <= SCORE_VARIANCE_TOLERANCE, (
        f"同一简历两次评分波动过大: {first.overall_score} vs {second.overall_score}"
    )
