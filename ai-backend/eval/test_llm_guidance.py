"""可选付费冒烟：只验证生产协议/引用，语义必须按 cases.review 人工核查。"""
import json
import os
from pathlib import Path

import pytest

from agent.graph import analyze
from agent.guidance_service import new_model
from agent.render import present
from agent.schema import AnalyzeRequest
from eval.test_auth import api, signup
from state.manager import GuidanceStore

CASES = json.loads((Path(__file__).parent / "cases/guidance.json").read_text())


@pytest.mark.llm
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_paid_guidance_contract(api, tmp_path, case):
    if os.getenv("RUN_PAID_GUIDANCE_EVAL") != "1":
        pytest.skip("需 RUN_PAID_GUIDANCE_EVAL=1 显式同意付费；不会自动读取 .env")
    user = signup(api.client)
    api.db.ensure_conversation_and_save_user("synthetic", "合成评测", user["id"])
    api.db.save_file_meta("synthetic", "synthetic.txt", len(case["resume_text"]), "synthetic", user["id"],
                         extracted_text=case["resume_text"])
    request = AnalyzeRequest(thread_id="synthetic", file_id="synthetic", target_role=case["target_role"], jd=case["jd"])
    result = analyze(request, user["id"], GuidanceStore(), new_model)
    (tmp_path / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (tmp_path / "report.md").write_text(present(result), encoding="utf-8")
    assert result.error_code is None, result.error_code
    assert result.guidance is not None
    if not case["jd"]:
        assert all(m.basis == "general" for m in result.guidance.matches)
    # 不用实体命中、分数稳定性等断言冒充语义真实性证明。
