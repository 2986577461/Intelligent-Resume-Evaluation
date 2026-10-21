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
    """跑一次完整评分图（resume_analyst -> 四维度 analyst -> report_generator），返回解析后的 report"""
    report_text = None
    for step in graph.stream({"resume_text": resume_text}):
        node = step.get("report")
        if node:
            report_text = node.get("report") if isinstance(node, dict) else node
    if not report_text:
        raise AssertionError("评分流程未产出 report 字段")
    return ReportSchema.model_validate_json(report_text)


def flatten_report_text(report: ReportSchema) -> str:
    """拼接 report 里所有自然语言字段，用于实体命中率检查"""
    parts = list(report.strengths) + list(report.risks) + list(report.interview_questions) + [report.summary]
    for dim in report.dimensions.values():
        parts.extend(dim.detail)
    return "\n".join(parts)
