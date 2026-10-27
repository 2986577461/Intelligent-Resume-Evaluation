"""简历指导协议：事实证据、投递上下文和可执行建议，不包含评分。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Extraction(StrictModel):
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


class ContextSignal(StrictModel):
    value: str = Field(min_length=1, max_length=16000)
    source: Literal["user", "resume"]
    evidence: str = Field(min_length=1, max_length=16000)
    inferred: bool = False


class GuidanceContext(StrictModel):
    position: ContextSignal | None = None
    application_type: ContextSignal | None = None
    job_description: ContextSignal | None = None

    @field_validator("application_type")
    @classmethod
    def valid_application_type(cls, value):
        if value is not None and value.value not in {"实习", "校招", "社招"}:
            raise ValueError("投递类型必须是实习、校招或社招")
        return value

    def missing_fields(self):
        return [key for key in ("position", "application_type")
                if getattr(self, key) is None or getattr(self, key).inferred]


class ContextResolution(GuidanceContext):
    clear_fields: list[Literal["position", "application_type", "job_description"]] = Field(default_factory=list)
    clear_evidence: str | None = None


class Finding(StrictModel):
    subject: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1, max_length=4)
    interpretation: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    action: Literal["保留", "突出", "压缩", "改写", "补证据", "待确认", "补实践"]
    priority: Literal["高", "中", "低"]
    interview_question: str | None = None


class AnalystResult(StrictModel):
    overview: str = Field(min_length=1)
    findings: list[Finding] = Field(max_length=12)


MODULE_LABELS = {"experience": "实习与工作经历", "project": "项目经历", "skill": "技术能力",
                 "education": "教育背景", "presentation": "表达与一致性"}
Module = Literal["experience", "project", "skill", "education", "presentation"]


class ReviewSection(StrictModel):
    module: Module
    commentary: str = Field(min_length=1)
    evidence: list[str] = Field(max_length=8)


class ActionItem(StrictModel):
    priority: Literal["高", "中", "低"]
    kind: Literal["表达调整", "补充证据", "能力实践", "信息确认"]
    action: str = Field(min_length=1)
    evidence: list[str] = Field(max_length=4)


class InterviewQuestion(StrictModel):
    question: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1, max_length=4)


class ReportIntroduction(StrictModel):
    match_status: Literal["有较充分支持", "部分支持", "存在明显差距", "信息不足"]
    overview: str = Field(min_length=1)


class Synthesis(ReportIntroduction):
    sections: list[ReviewSection] = Field(min_length=5, max_length=5)
    actions: list[ActionItem] = Field(min_length=1, max_length=5)
    interview_questions: list[InterviewQuestion] = Field(max_length=6)

    @model_validator(mode="after")
    def complete_sections(self):
        if {section.module for section in self.sections} != set(MODULE_LABELS):
            raise ValueError("指导模块必须完整且不重复")
        return self


class Report(Synthesis):
    context: GuidanceContext
