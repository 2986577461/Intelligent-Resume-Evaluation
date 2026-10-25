"""评测辅助函数：加载 golden set、跑评分图、抽取文本做 grounding 校验"""

import json
from pathlib import Path

from eval.schemas import GoldenResume, ReportSchema

GOLDEN_DIR = Path(__file__).parent / "golden_resumes"


def load_golden_resumes() -> list[tuple[str, GoldenResume]]:
    resumes = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        resumes.append((path.stem, GoldenResume(**data)))
    return resumes


def run_eval_pipeline(graph, resume_text: str) -> ReportSchema:
    """生产输出 Markdown，同时提供确定性构造的 report_data 供结构/质量评测。"""
    result = graph.invoke({"resume_text": resume_text, "page_count": 1})
    if not result.get("report_data"):
        raise AssertionError("评分流程未产出 report_data")
    return ReportSchema.model_validate(result["report_data"])


def flatten_report_text(report: ReportSchema) -> str:
    """拼接 report 里所有自然语言字段，用于实体命中率检查"""
    parts = list(report.strengths) + list(report.risks) + list(report.interview_questions) + [report.summary]
    for dim in report.dimensions.values():
        parts.extend(dim.detail)
    return "\n".join(parts)
