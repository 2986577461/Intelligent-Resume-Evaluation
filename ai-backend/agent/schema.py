"""生产评分协议；展示、缓存与评测共用，分数为已经加权的分数。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

DIMENSION_WEIGHTS = {
    "skill_match": 10,
    "project_depth": 30,
    "experience": 20,
    "education": 35,
    "layout": 5,
}
DIMENSION_LABELS = {
    "skill_match": "技能匹配", "project_depth": "项目深度", "experience": "工作/实习经历",
    "education": "教育背景", "layout": "版式篇幅",
}


class Extraction(BaseModel):
    # 旧提取字段保留；null 列表按提示词约定归一为空列表。
    model_config = ConfigDict(extra="forbid")
    position: str | None
    skills: list[str]
    education: list[dict]
    experiences: list[dict]
    projects: list[dict]

    @field_validator("skills", "education", "experiences", "projects", mode="before")
    @classmethod
    def null_to_list(cls, value):
        return [] if value is None else value

    @field_validator("position")
    @classmethod
    def normalize_position(cls, value):
        return (value.strip() or None) if value is not None else None


class AnalystResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_score: float = Field(ge=0, le=100, allow_inf_nan=False)
    detail: list[str] = Field(min_length=1, max_length=3)


class DimensionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float = Field(ge=0, le=100, allow_inf_nan=False)
    detail: list[str] = Field(min_length=1, max_length=6)

    @field_validator("detail")
    @classmethod
    def nonempty_details(cls, value):
        if any(not item.strip() for item in value):
            raise ValueError("评分理由不能为空")
        return value


def validate_dimension(value: dict, name: str) -> dict:
    result = DimensionResult.model_validate(value)
    if result.score > DIMENSION_WEIGHTS[name]:
        raise ValueError(f"{name} 超出维度上限")
    return result.model_dump()


class Synthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strengths: list[str] = Field(min_length=1, max_length=5)
    risks: list[str] = Field(min_length=1, max_length=5)
    interview_questions: list[str] = Field(min_length=1, max_length=6)
    summary: str = Field(min_length=1)


class Report(Synthesis):
    overall_score: float
    dimensions: dict[str, DimensionResult]
