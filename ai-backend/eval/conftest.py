"""pytest fixtures：真实 model / 评分图 / golden set 参数化"""

import os

import pytest
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from agent.graph import build_eval_graph
from eval.helpers import load_golden_resumes, run_eval_pipeline

load_dotenv()


@pytest.fixture(scope="session")
def real_model():
    return init_chat_model(model=os.getenv("MODEL_NAME", "deepseek-v4-flash"))


@pytest.fixture(scope="session")
def eval_graph(real_model):
    return build_eval_graph(real_model)


def _golden_params():
    return [pytest.param(gr, id=name) for name, gr in load_golden_resumes()]


@pytest.fixture(params=_golden_params())
def golden_resume(request):
    return request.param


# 同一份 golden resume 在 test_llm_schema.py / test_llm_grounding.py 里被多个测试函数复用，
# 按 resume_text 缓存一次流程结果，避免每个断言都重新触发一遍完整的真实 LLM 评分
_report_cache: dict[str, object] = {}


@pytest.fixture
def report(eval_graph, golden_resume):
    key = golden_resume.resume_text
    if key not in _report_cache:
        _report_cache[key] = run_eval_pipeline(eval_graph, golden_resume.resume_text)
    return _report_cache[key]
