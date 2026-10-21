"""生产/评测共用协议；引用存在不等于经历已经独立核实。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(StrictModel):
    id: str
    kind: Literal["resume", "jd", "user"]
    text: str = Field(min_length=1)
    # 仅用户明确勾选的“尚未做过/掌握”，不从自然语言否定推断。
    confirms_absence: bool = False
    requirement_id: str | None = None


class Fact(StrictModel):
    kind: Literal["skill", "project", "experience", "education", "other"]
    source_id: str
    quote: str = Field(min_length=1)


class Extraction(StrictModel):
    facts: list[Fact] = Field(min_length=1)


class Match(StrictModel):
    id: str
    requirement: str = Field(min_length=1)
    basis: Literal["jd", "general"]
    requirement_source_ids: list[str]
    evidence_source_ids: list[str]
    status: Literal["evidenced", "insufficient", "confirmed_gap"]
    observation: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class Action(StrictModel):
    priority: Literal[1, 2, 3]
    requirement_id: str
    source_ids: list[str]
    problem: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    next_step: str = Field(min_length=1)


class Question(StrictModel):
    id: str
    requirement_id: str
    question: str = Field(min_length=1)
    why: str = Field(min_length=1)


class Rewrite(StrictModel):
    source_id: str
    # 最小版本仅做有来源的整句摘编，不开放自由生成的事实性文案。
    sentences: list[str] = Field(min_length=1)
    supporting_source_ids: list[str]
    reason: str = Field(min_length=1)
    pending_confirmation: list[str]


class Guidance(StrictModel):
    matches: list[Match] = Field(min_length=1, max_length=12)
    actions: list[Action] = Field(min_length=1, max_length=12)
    questions: list[Question] = Field(max_length=3)
    rewrites: list[Rewrite] = Field(max_length=3)
    limitations: list[str]


class ProcessingFailure(StrictModel):
    stage: Literal["model_init", "extract", "guide"]
    exception_type: str
    http_status: int | None = None
    attempts: int


class OmittedComponent(StrictModel):
    component: Literal["rewrite"] = "rewrite"
    index: int = Field(ge=0, le=2)
    reason: Literal["unsafe_or_unverifiable_rewrite"] = "unsafe_or_unverifiable_rewrite"


class Result(StrictModel):
    schema_version: Literal["guidance.v1"] = "guidance.v1"
    id: str
    file_id: str
    target_role: str
    jd_used: bool
    status: Literal["success", "partial", "failed", "needs_target"]
    sources: list[Source]
    facts: list[Fact]
    guidance: Guidance | None
    limitations: list[str]
    error_code: str | None = None
    # 只持久化可安全展示的诊断；不包含异常正文、请求、密钥或简历。
    processing_error: ProcessingFailure | None = None
    # 仅保留拒绝原因，不保存或展示不安全文案；核心指导仍必须完整通过校验。
    omitted_components: list[OmittedComponent] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def integrity(self):
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("duplicate source id")
        if self.jd_used != any(s.kind == "jd" for s in self.sources):
            raise ValueError("JD usage inconsistent with sources")
        if self.status == "success" and (not self.target_role or not self.guidance or self.error_code or self.processing_error or self.omitted_components or self.guidance.questions or any(m.status == "insufficient" for m in self.guidance.matches)):
            raise ValueError("complete success requires complete validated guidance")
        if self.status == "needs_target" and (self.target_role or not self.facts or self.guidance):
            raise ValueError("invalid target confirmation state")
        return self


class GuidanceResponse(StrictModel):
    result: Result
    markdown: str
    user_message: str


class Answer(StrictModel):
    question_id: str
    text: str = Field(min_length=1, max_length=4000)
    confirms_absence: bool = False

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class AnalyzeRequest(StrictModel):
    thread_id: str = Field(min_length=1)
    file_id: str = Field(min_length=1)
    target_role: str = Field(default="", max_length=200)
    jd: str = Field(default="", max_length=20000)
    parent_id: str | None = None
    answers: list[Answer] = Field(default_factory=list, max_length=3)

    @field_validator("thread_id", "file_id", "target_role", "jd", mode="before")
    @classmethod
    def strip_input(cls, value):
        return value.strip() if isinstance(value, str) else value
