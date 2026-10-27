"""评测直接复用指导协议；测试投递场景由夹具明确指定，不是候选人事实。"""

from pydantic import BaseModel

from agent.schema import Report


class ReportSchema(Report):
    pass


class GoldenResume(BaseModel):
    resume_text: str
    position: str
    must_mention: list[str]
