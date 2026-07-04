"""真实调用 LLM 评测：验证 report 输出符合 schema、权重上限、总分求和。
产生 token 费用，默认不跑，用 `pytest -m llm` 显式执行。
"""

import pytest

from eval.schemas import SCORE_TIER_BANDS

pytestmark = pytest.mark.llm


def test_report_matches_schema(report):
    assert report.dimension_keys_valid(), f"维度 key 不完整: {list(report.dimensions.keys())}"


def test_dimension_scores_within_weight(report):
    errors = report.dimension_scores_within_weight()
    assert not errors, "; ".join(errors)


def test_overall_score_matches_dimension_sum(report):
    assert report.overall_score_matches_sum(), (
        f"overall_score={report.overall_score} 与维度分数之和不符: "
        f"{[(k, d.score) for k, d in report.dimensions.items()]}"
    )


def test_overall_score_within_tier_band(report, golden_resume):
    low, high = SCORE_TIER_BANDS[golden_resume.score_tier]
    assert low <= report.overall_score <= high, (
        f"score_tier={golden_resume.score_tier} 期望区间 [{low},{high}]，实际 {report.overall_score}"
    )
