"""report_generator.md 约定输出结构的 pydantic 校验模型"""

from pydantic import BaseModel, Field

# 对应 agent/graph.py 里 build_eval_graph 的权重设计
DIMENSION_WEIGHTS = {
    "skill_match": 10,
    "project_depth": 35,
    "experience": 20,
    "education": 35,
}


class DimensionResult(BaseModel):
    score: float
    detail: list[str]


class ReportSchema(BaseModel):
    overall_score: float
    dimensions: dict[str, DimensionResult]
    strengths: list[str]
    risks: list[str]
    interview_questions: list[str]
    summary: str

    def dimension_keys_valid(self) -> bool:
        return set(self.dimensions.keys()) == set(DIMENSION_WEIGHTS.keys())

    def dimension_scores_within_weight(self, tolerance: float = 1.0) -> list[str]:
        """返回超出对应维度权重上限的错误信息列表，空列表表示全部合规"""
        errors = []
        for name, result in self.dimensions.items():
            cap = DIMENSION_WEIGHTS.get(name)
            if cap is None:
                errors.append(f"未知维度: {name}")
            elif result.score > cap + tolerance:
                errors.append(f"{name} 分数 {result.score} 超出权重上限 {cap}")
        return errors

    def overall_score_matches_sum(self, tolerance: float = 2.0) -> bool:
        total = sum(d.score for d in self.dimensions.values())
        return abs(total - self.overall_score) <= tolerance


class GoldenResume(BaseModel):
    resume_text: str
    position: str
    score_tier: str = Field(pattern="^(weak|mid|strong)$")
    must_mention: list[str]


SCORE_TIER_BANDS = {
    "weak": (0, 45),
    "mid": (35, 75),
    "strong": (65, 100),
}
