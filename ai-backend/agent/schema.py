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


ActionId = Literal["A1", "A2", "A3"]


class ActionItem(StrictModel):
    id: ActionId
    subject: str = Field(min_length=1)
    priority: Literal["高", "中", "低"]
    kind: Literal["表达调整", "补充证据", "能力实践", "信息确认"]
    evidence: list[str] = Field(max_length=4)
    observation: str = Field(min_length=1, description="原文观察及问题；缺失信息明确说明未提供，不断言不会")
    impact: str = Field(min_length=1, description="为什么影响本次投递的理解")
    action: str = Field(min_length=1, description="用户可执行的下一步，不虚构事实")


class InterviewQuestion(StrictModel):
    action_id: ActionId | None = Field(description="关联建议 ID；无需修改但值得准备的问题为 null")
    subject: str = Field(min_length=1)
    question: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1, max_length=4)
    purpose: str = Field(min_length=1, description="为什么会问、希望澄清什么")
    preparation: str = Field(min_length=1, description="应准备的真实过程或证据，不是编造的参考答案")


class ReportIntroduction(StrictModel):
    overview: str = Field(min_length=1)


class Synthesis(ReportIntroduction):
    actions: list[ActionItem] = Field(max_length=3)
    interview_questions: list[InterviewQuestion] = Field(max_length=5)

    @model_validator(mode="after")
    def valid_action_references(self):
        ids = [item.id for item in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("建议 ID 不得重复")
        for question in self.interview_questions:
            if question.action_id is not None and question.action_id not in ids:
                raise ValueError("追问关联的建议不存在")
        return self


class Report(Synthesis):
    context: GuidanceContext
