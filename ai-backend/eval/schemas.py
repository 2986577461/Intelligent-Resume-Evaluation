"""评测复用生产协议，额外提供断言辅助方法。"""

from pydantic import BaseModel, Field

from agent.schema import DIMENSION_WEIGHTS, DimensionResult, Report


class ReportSchema(Report):
    def dimension_keys_valid(self) -> bool:
        return set(self.dimensions) == set(DIMENSION_WEIGHTS)

    def dimension_scores_within_weight(self, tolerance: float = 0) -> list[str]:
        errors = []
        for name, result in self.dimensions.items():
            cap = DIMENSION_WEIGHTS.get(name)
            if cap is None:
                errors.append(f"未知维度: {name}")
            elif not 0 <= result.score <= cap + tolerance:
                errors.append(f"{name} 分数 {result.score} 超出范围 [0, {cap}]")
        return errors

    def overall_score_matches_sum(self, tolerance: float = 0.01) -> bool:
        return abs(sum(d.score for d in self.dimensions.values()) - self.overall_score) <= tolerance


class GoldenResume(BaseModel):
    resume_text: str
    position: str
    score_tier: str = Field(pattern="^(weak|mid|strong)$")
    must_mention: list[str]


SCORE_TIER_BANDS = {"weak": (0, 45), "mid": (35, 75), "strong": (65, 100)}
