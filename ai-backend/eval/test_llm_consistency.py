"""重复真实调用的有限稳定性抽查，不替代人工语义审阅。显式 -m consistency 执行会计费。"""

import pytest

from eval.helpers import run_eval_pipeline

pytestmark = pytest.mark.consistency


def test_main_judgment_and_evidence_do_not_flip(eval_graph, golden_resume):
    # 校招是本评测显式指定的测试场景，不代表样本真实投递意图。
    first = run_eval_pipeline(eval_graph, golden_resume.resume_text, golden_resume.position, "校招")
    second = run_eval_pipeline(eval_graph, golden_resume.resume_text, golden_resume.position, "校招")
    assert {first.match_status, second.match_status} != {"有较充分支持", "存在明显差距"}
    first_evidence = {"".join(q.split()) for section in first.sections for q in section.evidence}
    second_evidence = {"".join(q.split()) for section in second.sections for q in section.evidence}
    assert any(a in b or b in a for a in first_evidence for b in second_evidence), "重复运行没有共同引用锚点"
