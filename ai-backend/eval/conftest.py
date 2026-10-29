"""真实模型指导评测夹具；固定校招测试场景，不推定样本真实求职类型。"""

import pytest

from agent.graph import build_eval_graph
from eval.helpers import load_golden_resumes, run_eval_pipeline


@pytest.fixture(scope="session")
def real_model():
    # 离线测试收集阶段不加载 .env，也不依赖真实模型的 SDK。
    from dotenv import load_dotenv
    from agent.scoring import create_scoring_model

    load_dotenv()
    return create_scoring_model()


@pytest.fixture(scope="session")
def eval_graph(real_model):
    return build_eval_graph(real_model)


def _golden_params():
    return [pytest.param(gr, id=name) for name, gr in load_golden_resumes()]


@pytest.fixture(params=_golden_params())
def golden_resume(request):
    return request.param


# 同一份 golden resume 在 test_llm_schema.py / test_llm_grounding.py 里被多个测试函数复用，
# 按 resume_text 缓存一次流程结果，避免每个断言重复执行完整的真实模型指导
_report_cache: dict[str, object] = {}


@pytest.fixture
def report(eval_graph, golden_resume):
    key = golden_resume.resume_text
    if key not in _report_cache:
        _report_cache[key] = run_eval_pipeline(eval_graph, golden_resume.resume_text, golden_resume.position, "校招")
    return _report_cache[key]
