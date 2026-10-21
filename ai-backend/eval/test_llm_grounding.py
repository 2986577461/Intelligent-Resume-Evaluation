"""真实调用 LLM 评测：校验 report 是否真的引用了简历里的具体实体（项目名/公司名/学校名），
而不是输出适用于所有人的泛化内容。产生 token 费用，用 `pytest -m llm` 显式执行。
"""

import pytest

from eval.helpers import flatten_report_text

pytestmark = pytest.mark.llm


def test_must_mention_entities_are_grounded(report, golden_resume):
    """report 全文（strengths/risks/interview_questions/各维度detail）里至少命中一个 must_mention 实体。
    多项目简历里模型常常只点名引用其中一两个项目、其余用描述性语言带过，
    这是正常写作变化而非"输出泛化内容"，因此这里只要求命中而非要求覆盖大多数实体。"""
    if not golden_resume.must_mention:
        pytest.skip("该 golden resume 未标注 must_mention")

    text = flatten_report_text(report)
    hits = [term for term in golden_resume.must_mention if term in text]
    assert hits, f"report 全文未命中任何 must_mention 实体 {golden_resume.must_mention}，疑似输出了泛化内容"


def test_interview_questions_reference_specifics(report, golden_resume):
    """至少一道追问命中 must_mention 实体，避免"你遇到过什么困难"式泛化提问。
    must_mention 只是简历里若干专有名词的抽样，追问引用其他具体技术细节（如某个库名、量化指标）
    同样算"具体"，因此这里只要求命中而非过半数，避免误伤真正具体但用词不同的追问。"""
    if not golden_resume.must_mention:
        pytest.skip("该 golden resume 未标注 must_mention")

    hit_count = sum(
        1 for q in report.interview_questions
        if any(term in q for term in golden_resume.must_mention)
    )
    assert hit_count >= 1, (
        f"{len(report.interview_questions)} 道追问均未引用 must_mention 实体 "
        f"{golden_resume.must_mention}，疑似输出了泛化提问"
    )
