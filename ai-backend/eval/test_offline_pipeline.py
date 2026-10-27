"""离线指导回归：假模型只验证协议、依赖、恢复及引用约束，不证明语义质量。"""

import copy
import json
import threading
import time
from collections import Counter

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk

from agent.graph import build_eval_graph
from agent.nodes import _call_llm, context_node
from agent.schema import Extraction, GuidanceContext, Report
from agent.scoring import evaluate_resume
from agent.telemetry import EvaluationTrace
from state.manager import CacheManager

# 历史记录不是本轮明确目标；缺项测试不能同时在标题里给出完整答案。
RESUME = ("历史目标记录：后端开发实习生\n示例大学 本科 2024.09-2026.06\n"
          "示例检索项目使用 Python 和 Redis，负责检索接口，使用合成数据验证。\n"
          "技能：Python、Redis、Spring Boot。\n")
EXTRACTION = {
    "position": "后端开发实习生", "skills": ["Python", "Redis", "Spring Boot"],
    "education": [{"school": "示例大学", "degree": "本科"}], "experiences": [],
    "projects": [{"name": "示例检索项目", "technologies": ["Python", "Redis"]}],
}


def signal(value, evidence, source="resume", inferred=False):
    return {"value": value, "evidence": evidence, "source": source, "inferred": inferred}


CONTEXT = {"position": signal("后端开发", "后端开发实习生"),
           "application_type": signal("实习", "后端开发实习生"), "job_description": None}
FINDING = {
    "subject": "示例检索项目", "evidence": ["示例检索项目使用 Python 和 Redis"],
    "interpretation": "原文提供了 Python 与 Redis 的实现线索。", "impact": "为后端方向提供部分证据。",
    "suggestion": "补充检索接口的真实验证方法，不虚构数据。", "action": "补证据", "priority": "高",
    "interview_question": "示例检索项目如何验证检索接口？",
}
REVIEW = {"overview": "已有实现线索，进一步核对验证方法。", "findings": [FINDING]}
SYNTHESIS = {
    "match_status": "部分支持", "overview": "示例检索项目提供了后端实现线索，尚不能确认具体企业门槛。",
    "sections": [
        {"module": "experience", "commentary": "简历未提供实习经历，不等于从未实习。", "evidence": []},
        {"module": "project", "commentary": "示例检索项目体现 Python 和 Redis 的使用，可补充验证方法。",
         "evidence": ["示例检索项目使用 Python 和 Redis"]},
        {"module": "skill", "commentary": "Spring Boot 目前缺少关联证据，可按目标岗位调整篇幅。",
         "evidence": ["技能：Python、Redis、Spring Boot。"]},
        {"module": "education", "commentary": "本科记录覆盖约两年，不能直接认定学制或升学路径。",
         "evidence": ["示例大学 本科 2024.09-2026.06"]},
        {"module": "presentation", "commentary": "可优先突出检索接口的个人工作，不凭页数判断视觉版式。",
         "evidence": ["负责检索接口"]},
    ],
    "actions": [{"priority": "高", "kind": "补充证据", "action": "补充示例检索项目已有的验证方法。",
                 "evidence": ["使用合成数据验证"]}],
    "interview_questions": [{"question": "示例检索项目为什么使用 Redis，如何验证？", "evidence": ["示例检索项目使用 Python 和 Redis"]}],
}
DATA = {"file_id": "file-a", "text": RESUME, "page_count": 2}


class MemoryCache:
    def __init__(self):
        self.values = {}

    def get_extraction(self, key):
        return copy.deepcopy(self.values.get(key))

    def set_extraction(self, key, value):
        self.values[key] = copy.deepcopy(value)


class MemoryStore:
    def __init__(self):
        self.values = {}

    def load(self, user_id, thread_id):
        return copy.deepcopy(self.values.get((user_id, thread_id)))

    def save(self, user_id, thread_id, file_id, extraction_key, extraction, context, status):
        self.values[user_id, thread_id] = copy.deepcopy(dict(
            file_id=file_id, extraction_key=extraction_key, extraction=extraction, context=context, status=status))


class ScriptedModel:
    def __init__(self, extraction=None, faults=None, parallel=False, contexts=None):
        self.extraction = copy.deepcopy(EXTRACTION if extraction is None else extraction)
        self.faults = copy.deepcopy(faults or {})
        self.contexts = copy.deepcopy(contexts or [])
        self.calls, self.finished = [], []
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(3) if parallel else None

    def invoke(self, messages, config=None):
        stage = config["run_name"].removeprefix("resume_")
        payload = None if stage == "extraction" else json.loads(messages[1].content)
        with self.lock:
            self.calls.append((stage, payload))
            faults = self.faults.get(stage, [])
            value = faults.pop(0) if faults else None
            if value is None and stage == "context":
                value = self.contexts.pop(0) if self.contexts else CONTEXT
        if self.barrier and stage in ("project", "experience", "education"):
            self.barrier.wait(timeout=5)
        if isinstance(value, Exception):
            raise value
        if value is None:
            if stage == "extraction":
                value = self.extraction
            else:
                from datetime import date
                assert payload["resume_text"].strip()
                assert payload["evaluation_date"] == date.today().isoformat()
                if stage == "synthesis":
                    assert set(self.finished) >= {"project", "skill", "experience", "education"}
                    assert set(payload["reviews"]) == {"project", "skill", "experience", "education"}
                    value = SYNTHESIS
                else:
                    if stage == "skill":
                        assert {"project", "experience"} <= set(self.finished)
                        assert payload["facts"]["projects"] == self.extraction["projects"]
                        assert payload["project_result"]["findings"]
                        assert "experience_result" in payload
                    value = REVIEW
        with self.lock:
            self.finished.append(stage)
        return AIMessage(content=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                         usage_metadata={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})


    def stream(self, messages, config=None):
        response = self.invoke(messages, config)
        stage = config["run_name"].removeprefix("resume_")
        with self.lock:
            self.finished.remove(stage)
        for index in range(0, len(response.content), 23):
            yield AIMessageChunk(content=response.content[index:index + 23])
        with self.lock:
            self.finished.append(stage)
        yield AIMessageChunk(content="", usage_metadata=response.usage_metadata)


def run(model=None, cache=None, data=None, store=None, user_message=""):
    chunks, states, metrics = [], [], []
    report = evaluate_resume(data or DATA, "alice", "thread", model or ScriptedModel(), cache,
                             user_message=user_message, store=store,
                             emit_state=states.append, emit_chunk=chunks.append, trace=EvaluationTrace(metrics.append))
    return report, chunks, states, metrics


def test_graph_dependencies_parallelism_and_contract():
    model = ScriptedModel(parallel=True)
    chunks = []
    result = build_eval_graph(model, emit_chunk=chunks.append).invoke({"resume_text": RESUME, "page_count": 2})
    report = Report.model_validate(result["report_data"])
    assert report.match_status == "部分支持"
    assert len(report.sections) == 5
    assert len(chunks) > 5 and "".join(chunks) == result["report"]
    assert "总分" not in result["report"] and "## 优先修改清单" in result["report"]
    assert "本次按目标岗位分析" in result["report"]
    assert "未提供具体 JD" not in result["report"]
    assert Counter(name for name, _ in model.calls) == Counter({
        "extraction": 1, "context": 1, "project": 1, "skill": 1, "experience": 1, "education": 1, "synthesis": 1})


def test_graph_stops_before_review_when_context_is_missing():
    model = ScriptedModel(contexts=[{}])
    result = build_eval_graph(model).invoke({"resume_text": RESUME})
    assert "report" not in result
    assert [name for name, _ in model.calls] == ["extraction", "context"]


def test_first_content_precedes_synthesis_completion_and_metrics_are_safe():
    model, times, metrics = ScriptedModel(), [], []
    report = evaluate_resume(DATA, "alice", "thread", model,
                             emit_chunk=lambda text: times.append((time.perf_counter(), set(model.finished))),
                             trace=EvaluationTrace(metrics.append))
    assert len(times) > 5 and "synthesis" not in times[0][1]
    assert "## 面试官可能继续追问" in report
    assert sum(m["event"] == "first_content" for m in metrics) == 1
    assert metrics[-1]["outcome"] == "success"
    assert any(m["event"] == "model_usage" and m["total_tokens"] == 30 for m in metrics)
    assert "示例大学" not in json.dumps(metrics, ensure_ascii=False)
    assert "后端开发" not in json.dumps(metrics, ensure_ascii=False)


def test_complete_guidance_returned_without_stream_callback():
    report = evaluate_resume(DATA, "alice", "thread", ScriptedModel())
    for title in ("总体判断", "实习与工作经历", "项目经历", "技术能力", "教育背景", "表达与一致性", "优先修改清单"):
        assert report.count(f"## {title}") == 1
    for forbidden in ("总分", "得分", "评分表", "建议缩减到1页"):
        assert forbidden not in report


def test_cached_extraction_reused_and_target_does_not_pollute_facts():
    cache = MemoryCache()
    new_context = {**CONTEXT, "position": signal("Python 开发", "Python 开发", "user")}
    model = ScriptedModel(contexts=[CONTEXT, new_context])
    run(model, cache)
    report, _, states, _ = run(model, cache, user_message="改投 Python 开发")
    assert Counter(name for name, _ in model.calls)["extraction"] == 1
    assert "Python 开发" in report and "已复用简历提取结果" in states
    assert next(iter(cache.values.values()))["position"] == EXTRACTION["position"]


def test_parse_first_then_resume_partial_context_without_redis():
    store = MemoryStore()
    partial = {"position": signal("后端", "后端", "user")}
    model = ScriptedModel(contexts=[partial, {"application_type": signal("校招", "校招", "user")}])
    first, chunks, _, _ = run(model, store=store, user_message="投后端，帮我指导简历")
    assert "实习、校招还是社招" in first and "什么岗位" not in first and not chunks
    assert [name for name, _ in model.calls] == ["extraction", "context"]
    assert store.load("alice", "thread")["status"] == "waiting"
    second, _, _, _ = run(model, store=store, user_message="校招")
    assert "目标岗位：后端｜投递类型：校招" in second
    assert Counter(name for name, _ in model.calls)["extraction"] == 1
    payload = [p for n, p in model.calls if n == "context"][-1]
    assert payload["previous_context"]["position"]["value"] == "后端"
    assert store.load("alice", "thread")["status"] == "ready"


def test_missing_both_fields_asked_together_and_no_jd_requirement():
    store = MemoryStore()
    report, chunks, _, _ = run(ScriptedModel(contexts=[{}]), store=store)
    assert "什么岗位" in report and "实习、校招还是社招" in report and "JD" not in report
    assert not chunks


def test_only_missing_position_is_asked():
    context = {"application_type": CONTEXT["application_type"]}
    report, _, _, _ = run(ScriptedModel(contexts=[context]))
    assert "什么岗位" in report and "还是社招" not in report and "已暂存" not in report


def test_inferred_graduation_type_requires_confirmation():
    context = {**CONTEXT, "application_type": signal("校招", "2024.09-2026.06", inferred=True)}
    store = MemoryStore()
    report, _, _, _ = run(ScriptedModel(contexts=[context]), store=store)
    assert "实习、校招还是社招" in report
    assert store.load("alice", "thread")["context"]["application_type"]["inferred"]


def test_new_file_does_not_inherit_previous_target():
    store, model = MemoryStore(), ScriptedModel(contexts=[CONTEXT, {}])
    run(model, store=store)
    report, _, _, _ = run(model, store=store, data={**DATA, "file_id": "file-b"})
    assert "什么岗位" in report
    assert [p for n, p in model.calls if n == "context"][-1]["previous_context"]["position"] is None
    assert Counter(name for name, _ in model.calls)["extraction"] == 2


def test_local_snapshot_reextracts_on_version_change_but_keeps_confirmed_context(monkeypatch):
    store, model = MemoryStore(), ScriptedModel()
    run(model, store=store)
    monkeypatch.setattr("agent.scoring._extraction_version", lambda: "new-version")
    run(model, store=store)
    assert Counter(name for name, _ in model.calls)["extraction"] == 2
    assert [p for n, p in model.calls if n == "context"][-1]["previous_context"] == CONTEXT


def test_role_and_type_without_jd_produce_complete_guidance_without_clarification():
    store, model = MemoryStore(), ScriptedModel()
    report, chunks, _, metrics = run(model, store=store)
    assert "本次按目标岗位分析" in report
    assert "优先修改清单" in report and "报告未完成" not in report
    assert "你这次" not in report and "未提供具体 JD" not in report
    assert "".join(chunks) == report and metrics[-1]["outcome"] == "success"
    snapshot = store.load("alice", "thread")
    assert snapshot["status"] == "ready" and snapshot["context"]["job_description"] is None
    for stage, payload in model.calls:
        if stage in {"project", "experience", "education", "skill", "synthesis"}:
            assert payload["context"]["position"] == CONTEXT["position"]
            assert payload["context"]["job_description"] is None


def test_user_message_and_jd_reach_context_and_review():
    message = "投 AI 应用开发实习，招聘要求：Python，负责检索服务。"
    context = {"position": signal("AI 应用开发", "AI 应用开发", "user"),
               "application_type": signal("实习", "实习", "user"),
               "job_description": signal("Python，负责检索服务。", "招聘要求：Python，负责检索服务。", "user")}
    model = ScriptedModel(contexts=[context])
    report, _, _, _ = run(model, user_message=message)
    assert "本次结合目标岗位与您提供的招聘要求（JD）分析" in report
    assert "本次按目标岗位分析" not in report
    assert [p for n, p in model.calls if n == "context"][0]["user_message"] == message
    for stage, payload in model.calls:
        if stage in {"project", "experience", "education", "skill", "synthesis"}:
            assert payload["context"]["job_description"] == context["job_description"]


def test_resume_cannot_override_previous_user_target():
    previous = {**CONTEXT, "position": signal("AI 应用开发", "AI 应用开发", "user")}
    state = {"resume_text": RESUME, "user_message": "继续", "previous_context": previous}
    result = context_node(state, ScriptedModel())
    assert result["context"]["position"] == previous["position"]


def test_explicit_target_withdrawal_is_not_silently_restored():
    previous = copy.deepcopy(CONTEXT)
    message = "我不投后端了，还没想好"
    resolution = {"clear_fields": ["position"], "clear_evidence": message}
    result = context_node({"resume_text": RESUME, "user_message": message, "previous_context": previous},
                          ScriptedModel(contexts=[resolution]))
    context = GuidanceContext.model_validate(result["context"])
    assert context.position is None
    assert context.application_type.value == "实习"
    assert context.missing_fields() == ["position"]
    with pytest.raises(ValueError):
        context_node({"resume_text": RESUME, "user_message": "继续", "previous_context": previous},
                     ScriptedModel(contexts=[resolution]))


def test_inferred_type_cannot_be_promoted_by_reinterpreting_the_same_resume():
    previous = {**CONTEXT, "application_type": signal("校招", "2024.09-2026.06", inferred=True)}
    invalid = {**previous, "application_type": signal("校招", "2024.09-2026.06")}
    with pytest.raises(ValueError):
        context_node({"resume_text": RESUME, "user_message": "继续", "previous_context": previous},
                     ScriptedModel(contexts=[invalid]))


def test_user_confirms_inferred_type_without_losing_position():
    previous = {**CONTEXT, "application_type": signal("校招", "2024.09-2026.06", inferred=True)}
    confirmed = {"application_type": signal("校招", "校招", "user")}
    result = context_node({"resume_text": RESUME, "user_message": "校招", "previous_context": previous},
                          ScriptedModel(contexts=[confirmed]))
    context = GuidanceContext.model_validate(result["context"])
    assert not context.missing_fields() and context.position.value == "后端开发"


@pytest.mark.parametrize("field,value", [("user", "bob"), ("thread", "other"), ("file", "other"),
                                         ("text", "changed"), ("version", "v2")])
def test_cache_keys_isolate_ownership_content_and_version(field, value):
    args = dict(user="alice", thread="thread", file="file-a", text="resume", version="v1")
    base = CacheManager.extraction_key(*args.values())
    args[field] = value
    assert base != CacheManager.extraction_key(*args.values())
    assert "alice" not in base


@pytest.mark.parametrize("operation", ["read", "write", "corrupt"])
def test_cache_failure_does_not_block_guidance(operation):
    class BrokenCache(MemoryCache):
        def get_extraction(self, key):
            if operation == "read":
                raise ConnectionError("PRIVATE SECRET")
            return {"raw": "invalid"} if operation == "corrupt" else None

        def set_extraction(self, key, value):
            if operation == "write":
                raise ConnectionError("PRIVATE SECRET")

    report, _, _, metrics = run(cache=BrokenCache())
    assert "总体判断" in report and metrics[-1]["outcome"] == "success"
    assert "PRIVATE SECRET" not in json.dumps(metrics)


def test_only_failed_node_retries_and_output_is_not_duplicated():
    model = ScriptedModel(faults={"project": ["not json"], "synthesis": [TimeoutError("temporary")]})
    report, chunks, _, metrics = run(model)
    counts = Counter(stage for stage, _ in model.calls)
    assert counts["project"] == counts["synthesis"] == 2
    assert counts["extraction"] == counts["skill"] == counts["education"] == 1
    assert "".join(chunks) == report and report.count("## 总体判断") == 1
    assert any(m.get("node") == "project" and m.get("attempt") == 2 for m in metrics)


def test_extraction_failure_never_reviews_or_caches_raw_output():
    cache, store = MemoryCache(), MemoryStore()
    model = ScriptedModel(faults={"extraction": ["truncated", "truncated"]})
    report, chunks, _, metrics = run(model, cache, store=store)
    assert "失败" in report and not chunks and not cache.values and not store.values
    assert [stage for stage, _ in model.calls] == ["extraction", "extraction"]
    assert metrics[-1]["outcome"] == "failed"


def test_context_failure_preserves_successful_extraction_for_retry():
    store = MemoryStore()
    model = ScriptedModel(faults={"context": ["invalid", "invalid"]})
    report, chunks, _, _ = run(model, store=store)
    assert "失败" in report and not chunks
    assert store.load("alice", "thread")["extraction"] == EXTRACTION
    second, _, _, _ = run(model, store=store)
    assert "总体判断" in second
    assert Counter(stage for stage, _ in model.calls)["extraction"] == 1


def test_synthesis_failure_does_not_emit_partial_analysis():
    report, chunks, _, metrics = run(ScriptedModel(faults={"synthesis": ["invalid", "invalid"]}))
    assert report == "简历指导生成失败，请稍后重试。" and not chunks
    assert not any(m["event"] == "first_content" for m in metrics)


@pytest.mark.parametrize("stage", ["project", "synthesis", "context"])
def test_invented_evidence_is_rejected(stage):
    if stage == "project":
        bad = copy.deepcopy(REVIEW)
        bad["findings"][0]["evidence"] = ["不存在的千万用户与性能指标"]
    elif stage == "synthesis":
        bad = copy.deepcopy(SYNTHESIS)
        bad["sections"][0]["evidence"] = ["不存在的公司"]
    else:
        bad = {**CONTEXT, "position": signal("算法", "并未提供的目标", "user")}
    report, chunks, _, _ = run(ScriptedModel(faults={stage: [bad, bad]}))
    if stage == "synthesis":
        assert "报告未完成" in report and "不存在的公司" not in report
        assert "".join(chunks) == report
    else:
        assert "失败" in report and not chunks


def test_whitespace_only_evidence_is_rejected():
    bad = copy.deepcopy(REVIEW)
    bad["findings"][0]["evidence"] = ["  "]
    report, chunks, _, _ = run(ScriptedModel(faults={"project": [bad, bad]}))
    assert "失败" in report and not chunks


@pytest.mark.parametrize("content", ["invalid", "[]", "null", '"text"'])
def test_call_llm_rejects_invalid_or_nonobject_json(content):
    with pytest.raises(ValueError):
        _call_llm("prompt", "text", FakeListChatModel(responses=[content]))


@pytest.mark.parametrize("fence", ["json", ""])
def test_call_llm_parses_fences(fence):
    value = '{"overview":"test","findings":[]}'
    assert _call_llm("prompt", "text", FakeListChatModel(responses=[f"```{fence}\n{value}\n``` "]))["overview"] == "test"


def test_schema_rejects_scores_and_incomplete_sections():
    assert Extraction.model_validate({**EXTRACTION, "projects": None}).projects == []
    with pytest.raises(ValueError):
        Extraction.model_validate({"position": None})
    with pytest.raises(ValueError):
        Report.model_validate({**SYNTHESIS, "context": CONTEXT, "overall_score": 90})
    bad = copy.deepcopy(SYNTHESIS)
    bad["sections"][0]["module"] = "project"
    with pytest.raises(ValueError):
        Report.model_validate({**bad, "context": CONTEXT})
    with pytest.raises(ValueError):
        GuidanceContext.model_validate({"application_type": signal("应届", "应届")})


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
    assert captured["api_base"] == "https://example.invalid/v1" and captured["max_retries"] == 0
    assert captured["extra_body"]["thinking"]["type"] == "disabled"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["timeout"] > 0 and captured["max_tokens"] == 8192
