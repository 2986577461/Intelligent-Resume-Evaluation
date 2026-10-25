"""离线工作流测试：按节点响应，避免并发下按调用顺序分配假数据。"""

import copy
import json
import threading
import time
from collections import Counter

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

from agent.graph import build_eval_graph, build_scoring_graph
from agent.nodes import _call_llm, layout_analyst_node
from agent.schema import Extraction, Report, validate_dimension
from agent.scoring import evaluate_resume
from agent.telemetry import EvaluationTrace
from state.manager import CacheManager

EXTRACTION = {
    "position": "后端开发实习生", "skills": ["Python", "Redis"],
    "education": [{"school": "示例大学", "greade": "本科"}], "experiences": [],
    "projects": [{"项目名称": "示例检索项目", "使用的工具或技术": ["Python", "Redis"]}],
}
SYNTHESIS = {
    "strengths": ["示例检索项目体现了 Python 的使用。"],
    "risks": ["Redis 的性能验证数据尚未提供，可补充可复现实验。"],
    "interview_questions": ["示例检索项目为什么使用 Redis？"],
    "summary": "先验证示例检索项目的关键设计，不根据文本推断录用概率。",
}
DATA = {"file_id": "file-a", "text": "synthetic resume", "page_count": 1}


class MemoryCache:
    def __init__(self):
        self.values = {}

    def get_extraction(self, key):
        return copy.deepcopy(self.values.get(key))

    def set_extraction(self, key, value):
        self.values[key] = copy.deepcopy(value)


class ScriptedModel:
    def __init__(self, extraction=None, faults=None, parallel=False):
        self.extraction = copy.deepcopy(EXTRACTION if extraction is None else extraction)
        self.faults = copy.copy(faults or {})
        self.calls = []
        self.finished = []
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(3) if parallel else None

    def invoke(self, messages, config=None):
        stage = config["run_name"].removeprefix("resume_")
        text = messages[1].content
        payload = None if stage == "extraction" else json.loads(text)
        with self.lock:
            self.calls.append((stage, payload))
            fault = self.faults.get(stage, [])
            value = fault.pop(0) if fault else None
        if self.barrier and stage in ("project", "experience", "education"):
            self.barrier.wait(timeout=5)
        if isinstance(value, Exception):
            raise value
        if value is None:
            if stage == "extraction":
                value = self.extraction
            elif stage == "synthesis":
                assert payload["resume_text"].strip()
                from datetime import date
                assert payload["evaluation_date"] == date.today().isoformat()
                assert set(self.finished) >= {"project", "skill", "experience", "education"}
                value = SYNTHESIS
            else:
                if stage == "skill":
                    assert "project" in self.finished
                    assert payload["projects"] == self.extraction["projects"]
                    assert payload["project_result"]["detail"] == ["示例检索项目使用 Python"]
                    assert "experiences" in payload
                    assert payload["resume_text"].strip()
                value = {"raw_score": {"project": 70, "skill": 80, "experience": 35, "education": 60}[stage],
                         "detail": ["示例检索项目使用 Python"]}
        with self.lock:
            self.finished.append(stage)
        return AIMessage(content=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                         usage_metadata={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})


def run(model=None, cache=None, data=None, position=None):
    chunks, states, metrics = [], [], []
    model = model or ScriptedModel()
    report = evaluate_resume(data or DATA, "alice", "thread", model, cache, position,
                             states.append, chunks.append, EvaluationTrace(metrics.append))
    return report, chunks, states, metrics


def test_graph_dependencies_parallelism_and_contract():
    model = ScriptedModel(parallel=True)
    chunks = []
    result = build_eval_graph(model, emit_chunk=chunks.append).invoke({"resume_text": "synthetic", "page_count": 2})
    report = Report.model_validate(result["report_data"])
    assert report.overall_score == sum(v.score for v in report.dimensions.values()) == 57
    assert report.dimensions["layout"].score == 0
    assert any("页" in item for item in report.risks)
    assert len(chunks) == 1
    assert "总分：57" in chunks[0] and "## 优势" in chunks[0]
    assert chunks == [result["report"]]
    assert "# 简历评估报告" not in chunks[0]
    assert chunks[0].index("## 总结") < chunks[0].index("> 以上是基于简历文本")
    assert Counter(name for name, _ in model.calls) == Counter({
        "extraction": 1, "project": 1, "skill": 1, "experience": 1, "education": 1, "synthesis": 1})


def test_first_content_follows_synthesis_and_metrics_are_safe():
    model = ScriptedModel()
    times = []
    metrics = []

    def output(text):
        times.append((time.perf_counter(), set(model.finished)))

    report = evaluate_resume(DATA, "alice", "thread", model, emit_chunk=output,
                             trace=EvaluationTrace(metrics.append))
    assert len(times) == 1 and "synthesis" in times[0][1]
    assert "## 针对性面试追问" in report
    assert sum(m["event"] == "first_content" for m in metrics) == 1
    assert metrics[-1]["outcome"] == "success"
    assert any(m["event"] == "model_usage" and m["total_tokens"] == 30 for m in metrics)
    assert "synthetic resume" not in json.dumps(metrics)
    assert "后端开发" not in json.dumps(metrics, ensure_ascii=False)


def test_complete_analysis_is_returned_without_stream_callback():
    report = evaluate_resume(DATA, "alice", "thread", ScriptedModel())
    assert "总分" in report
    for title in ("优势", "风险与改进建议", "针对性面试追问", "总结"):
        assert report.count(f"## {title}") == 1
    assert "# 简历评估报告" not in report


def test_cached_extraction_reused_and_position_does_not_pollute_cache():
    cache = MemoryCache()
    model = ScriptedModel()
    run(model, cache)
    report, _, states, _ = run(model, cache, position="Python 开发")
    assert Counter(stage for stage, _ in model.calls)["extraction"] == 1
    assert "Python 开发" in report and "已复用简历提取结果" in states
    assert next(iter(cache.values.values()))["position"] == EXTRACTION["position"]


def test_missing_position_followup_reuses_facts():
    cache = MemoryCache()
    extraction = {**EXTRACTION, "position": None}
    model = ScriptedModel(extraction=extraction)
    first, chunks, _, _ = run(model, cache)
    assert "什么岗位" in first and not chunks
    assert [stage for stage, _ in model.calls] == ["extraction"]
    second, _, _, _ = run(model, cache, position="后端实习生")
    assert "总分" in second
    assert Counter(stage for stage, _ in model.calls)["extraction"] == 1


@pytest.mark.parametrize("field,value", [("user", "bob"), ("thread", "other"), ("file", "other"),
                                         ("text", "changed"), ("version", "v2")])
def test_cache_keys_isolate_ownership_content_and_version(field, value):
    args = dict(user="alice", thread="thread", file="file-a", text="resume", version="v1")
    base = CacheManager.extraction_key(*args.values())
    args[field] = value
    assert base != CacheManager.extraction_key(*args.values())
    assert "alice" not in base and "resume" not in base.split(":")[-1]


@pytest.mark.parametrize("operation", ["read", "write", "corrupt"])
def test_cache_failure_does_not_block_scoring(operation):
    class BrokenCache(MemoryCache):
        def get_extraction(self, key):
            if operation == "read":
                raise ConnectionError("PRIVATE SECRET")
            return {"raw": "invalid"} if operation == "corrupt" else None

        def set_extraction(self, key, value):
            if operation == "write":
                raise ConnectionError("PRIVATE SECRET")

    report, _, _, metrics = run(cache=BrokenCache())
    assert "总分" in report and metrics[-1]["outcome"] == "success"
    assert "PRIVATE SECRET" not in json.dumps(metrics)


def test_only_failed_node_retries_and_output_is_not_duplicated():
    model = ScriptedModel(faults={"project": ["not json"], "synthesis": [TimeoutError("temporary")]})
    report, chunks, _, metrics = run(model)
    counts = Counter(stage for stage, _ in model.calls)
    assert counts["project"] == counts["synthesis"] == 2
    assert counts["extraction"] == counts["skill"] == counts["education"] == 1
    assert chunks == [report] and report.count("**总分：") == 1
    assert report.count("## 优势") == 1
    assert any(m.get("node") == "project" and m.get("attempt") == 2 for m in metrics)


def test_extraction_failure_never_scores_or_caches_raw_output():
    cache = MemoryCache()
    model = ScriptedModel(faults={"extraction": ["truncated", "truncated"]})
    report, chunks, _, metrics = run(model, cache)
    assert "失败" in report and not chunks and not cache.values
    assert [stage for stage, _ in model.calls] == ["extraction", "extraction"]
    assert metrics[-1]["outcome"] == "failed"


def test_synthesis_failure_does_not_emit_partial_analysis():
    report, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": ["invalid", "invalid"]}))
    assert report == "简历分析失败，请稍后重试。"
    assert not chunks
    assert not any(m["event"] == "first_content" for m in metrics)
    assert metrics[-1]["outcome"] == "failed"


def test_out_of_range_dimension_rejected_before_total():
    bad = {"raw_score": 140, "detail": ["invalid"]}
    report, chunks, _, _ = run(ScriptedModel(faults={"project": [bad, bad]}))
    assert "失败" in report and not chunks


@pytest.mark.parametrize("content", ["invalid", "[]", "null", '"text"'])
def test_call_llm_rejects_invalid_or_nonobject_json(content):
    with pytest.raises(ValueError):
        _call_llm("prompt", "text", FakeListChatModel(responses=[content]))


@pytest.mark.parametrize("fence", ["json", ""])
def test_call_llm_parses_fences(fence):
    value = '{"score":8,"detail":["reason"]}'
    assert _call_llm("prompt", "text", FakeListChatModel(responses=[f"```{fence}\n{value}\n``` "]))["score"] == 8


def test_layout_has_no_model_and_invalid_pages_are_rejected():
    assert layout_analyst_node({"page_count": 1})["layout_result"]["score"] == 5
    assert layout_analyst_node({"page_count": 3})["layout_result"]["score"] == 0
    for value in [0, -1, "2", True]:
        with pytest.raises(ValueError):
            layout_analyst_node({"page_count": value})


def test_null_lists_are_normalized_and_missing_fields_are_not_invented():
    assert Extraction.model_validate({**EXTRACTION, "projects": None}).projects == []
    with pytest.raises(ValueError):
        Extraction.model_validate({"position": None})
    with pytest.raises(ValueError):
        validate_dimension({"score": -1, "detail": ["bad"]}, "skill_match")


def test_authentication_error_does_not_retry():
    class AuthenticationError(Exception):
        status_code = 401
    model = ScriptedModel(faults={"extraction": [AuthenticationError("secret")]})
    report, _, _, _ = run(model)
    assert "失败" in report and len(model.calls) == 1


def test_model_uses_json_mode_and_explicit_no_thinking(monkeypatch):
    pytest.importorskip("langchain")
    from agent.scoring import create_scoring_model
    captured = {}

    class Model:
        def bind(self, **kwargs):
            captured.update(kwargs)
            return self

    def init(**kwargs):
        captured.update(kwargs)
        return Model()

    monkeypatch.setenv("MODEL_NAME", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setattr("langchain.chat_models.init_chat_model", init)
    create_scoring_model()
    assert captured["api_base"] == "https://example.invalid/v1"
    assert captured["max_retries"] == 0
    assert captured["extra_body"]["thinking"]["type"] == "disabled"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["timeout"] > 0
