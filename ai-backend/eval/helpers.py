"""加载固定样本、运行指导图和收集可审计文本。"""

import json
from pathlib import Path

from eval.schemas import GoldenResume, ReportSchema

GOLDEN_DIR = Path(__file__).parent / "golden_resumes"


def load_golden_resumes():
    return [(path.stem, GoldenResume(**json.loads(path.read_text(encoding="utf-8"))))
            for path in sorted(GOLDEN_DIR.glob("*.json"))]


def run_eval_pipeline(graph, resume_text, position, application_type):
    result = graph.invoke({"resume_text": resume_text, "page_count": 1,
                           "user_message": f"请以{application_type}为本次投递场景，目标岗位是{position}，指导这份简历。"})
    if not result.get("report_data"):
        raise AssertionError("指导流程未产出 report_data")
    return ReportSchema.model_validate(result["report_data"])


def flatten_report_text(report):
    parts = [report.overview]
    for item in report.actions:
        parts.extend([item.subject, item.observation, item.impact, item.action])
    for item in report.interview_questions:
        parts.extend([item.question, item.purpose, item.preparation])
    return "\n".join(parts)
