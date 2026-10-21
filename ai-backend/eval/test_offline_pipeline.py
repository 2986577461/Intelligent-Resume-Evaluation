"""验证数据流/边界，不用 mock 声称证明模型语义不会幻觉。"""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from agent.graph import analyze, source_units
from agent.nodes import Budget, call_structured, validate_guidance
from agent.schema import AnalyzeRequest, Guidance, Result
from eval.test_auth import api, signup  # 复用真实路由/临时 DB fixture

TEXT = "项目：用 Python 实现订单 API。\n优化了接口性能。"
JD = "熟悉 Python\n了解 Redis"


class ControlledModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if not self.responses:
            raise AssertionError("unexpected extra model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(json.loads(messages[-1].content))
        return SimpleNamespace(content=response if isinstance(response, str) else json.dumps(response, ensure_ascii=False))


def extraction():
    src = source_units(TEXT, "resume")
    return {"facts": [{"kind": "project", "source_id": src[0].id, "quote": src[0].text}]}


def guidance(payload):
    resume = [s for s in payload["sources"] if s["kind"] == "resume"]
    jd = [s for s in payload["sources"] if s["kind"] == "jd"]
    return {
        "matches": [{"id": "r1", "requirement": jd[0]["text"] if jd else "Python 接口开发",
                     "basis": "jd" if jd else "general", "requirement_source_ids": [jd[0]["id"]] if jd else [],
                     "evidence_source_ids": [resume[0]["id"]], "status": "evidenced",
                     "observation": "项目写明 Python API", "rationale": "原文有接口实现描述，但性能结论没有验证过程"},
                    {"id": "r2", "requirement": jd[1]["text"] if jd else "缓存使用经验",
                     "basis": "jd" if jd else "general", "requirement_source_ids": [jd[1]["id"]] if jd else [],
                     "evidence_source_ids": [], "status": "insufficient",
                     "observation": "简历未提供缓存使用信息", "rationale": "未提供不等于未掌握，需用户确认"}],
        "actions": [{"priority": 1, "requirement_id": "r1", "source_ids": [resume[1]["id"]],
                     "problem": "性能优化结论缺少验证过程", "rationale": "原文未描述测试方法",
                     "next_step": "重现订单查询场景，记录测试数据规模、并发条件、优化前后耗时；无法复测时只描述改动与验证方法，不写提升百分比"}],
        "questions": [{"id": "q1", "requirement_id": "r2", "question": "订单 API 是否实际使用过 Redis？请说明用途或确认未使用。",
                       "why": "决定补充项目证据还是制定学习验证步骤"}],
        "rewrites": [], "limitations": [],
    }


@pytest.fixture
def case(api):
    from state.manager import GuidanceStore
    user = signup(api.client)
    api.db.ensure_conversation_and_save_user("thread", "hello", user["id"])
    api.db.save_file_meta("file", "synthetic.pdf", len(TEXT), "thread", user["id"], extracted_text=TEXT)
    return SimpleNamespace(api=api, user=user["id"], store=GuidanceStore())


def run(case, model, **kwargs):
    req = AnalyzeRequest(thread_id="thread", file_id="file", target_role="后端开发", jd=JD, **kwargs)
    return analyze(req, case.user, case.store, lambda: model, Budget(seconds=5, attempts=1))


def test_jd_analysis_data_flow_and_storage(case):
    model = ControlledModel(extraction(), guidance)
    result = run(case, model)
    assert result.status == "partial" and result.error_code is None
    assert result.guidance.matches[0].basis == "jd"
    assert result.guidance.actions[0].source_ids == [source_units(TEXT, "resume")[1].id]
    payload = json.loads(model.calls[1][0][-1].content)
    assert len(payload["sources"]) == 4  # 技能判断同时看到完整项目/经历，不只技能表
    assert payload["facts"][0]["quote"] == extraction()["facts"][0]["quote"]
    stored = case.store.result(result.id, case.user, "thread", "file")
    assert stored == result and Result.model_validate_json(stored.model_dump_json()) == result
    assert model.calls[0][1]["timeout"] <= 5
    assert model.calls[0][1]["config"]["callbacks"] == []


def test_no_jd_general_only_and_fake_company_provenance_rejected(case):
    def fake_jd(payload):
        g = guidance(payload)
        g["matches"][0]["basis"] = "jd"
        g["matches"][0]["requirement_source_ids"] = ["jd:invented"]
        return g
    req = AnalyzeRequest(thread_id="thread", file_id="file", target_role="后端开发")
    model = ControlledModel(extraction(), guidance)
    result = analyze(req, case.user, case.store, lambda: model, Budget(attempts=1))
    assert not result.jd_used and all(m.basis == "general" for m in result.guidance.matches)
    assert any("一般要求" in x for x in result.limitations)
    bad = analyze(req, case.user, case.store, lambda: ControlledModel(fake_jd), Budget(attempts=1))
    assert bad.error_code == "invalid_output" and bad.status == "partial" and bad.guidance is None


def test_missing_target_then_reuses_extraction(case):
    first_model = ControlledModel(extraction())
    first = analyze(AnalyzeRequest(thread_id="thread", file_id="file"), case.user, case.store,
                    lambda: first_model, Budget(attempts=1))
    assert first.status == "needs_target" and first.facts and first.guidance is None
    second_model = ControlledModel(guidance)
    second = run(case, second_model, parent_id=first.id)
    assert len(first_model.calls) == len(second_model.calls) == 1
    assert second.facts == first.facts
    assert [s.id for s in second.sources if s.kind == "resume"] == [s.id for s in first.sources]


def test_missing_skill_is_not_confirmed_gap(case):
    def fabricated_gap(payload):
        g = guidance(payload)
        g["matches"][1]["status"] = "confirmed_gap"
        return g
    result = run(case, ControlledModel(extraction(), fabricated_gap))
    assert result.error_code == "invalid_output" and result.guidance is None
    next_result = run(case, ControlledModel(guidance))
    assert next_result.guidance.matches[1].status == "insufficient"


@pytest.mark.parametrize("broken", ["not JSON", "{}", '{"facts": [], "raw": "bad"}',
                                      '{"facts": [{"kind":"skill", "source_id":"fake", "quote":"Python"}]}'])
def test_invalid_extraction_is_failure_not_empty_success(case, broken):
    result = run(case, ControlledModel(broken))
    assert result.status == "failed" and result.error_code == "invalid_output"
    assert result.facts == [] and result.guidance is None
    assert case.store.extraction("file", TEXT) is None


@pytest.mark.parametrize("corruption", ["citation", "missing_field", "new_number", "new_tech", "negation_cut", "unknown_requirement"])
def test_guidance_validation_blocks_invalid_data(case, corruption):
    def bad(payload):
        g = guidance(payload)
        sid = payload["sources"][0]["id"]
        if corruption == "citation":
            g["actions"][0]["source_ids"] = ["resume:fake"]
        elif corruption == "missing_field":
            del g["actions"][0]["rationale"]
        elif corruption == "unknown_requirement":
            g["actions"][0]["requirement_id"] = "another"
        else:
            version = {"new_number": "性能提升 80%。", "new_tech": "用 Kafka 实现订单 API。", "negation_cut": "用 Python 实现订单 API。"}[corruption]
            g["rewrites"] = [{"source_id": sid, "sentences": [version], "supporting_source_ids": [],
                              "reason": "精简", "pending_confirmation": []}]
        return g
    result = run(case, ControlledModel(extraction(), bad))
    if corruption in {"new_number", "new_tech", "negation_cut"}:
        assert result.status == "partial" and result.error_code is None
        assert result.guidance and result.guidance.actions and result.guidance.rewrites == []
        assert len(result.omitted_components) == 1
    else:
        assert result.status == "partial" and result.error_code == "invalid_output" and result.guidance is None


def test_bad_optional_rewrite_keeps_valid_core_without_false_complete_status(case):
    from agent import render
    def partially_valid(payload):
        g = guidance(payload)
        g["matches"] = g["matches"][:1]
        g["questions"] = []
        sid = payload["sources"][0]["id"]
        g["rewrites"] = [
            {"source_id": sid, "sentences": [TEXT.splitlines()[0]], "supporting_source_ids": [],
             "reason": "保留原句", "pending_confirmation": []},
            {"source_id": sid, "sentences": ["使用 Kafka 后性能提升 80%。"], "supporting_source_ids": [],
             "reason": "不应展示的改写", "pending_confirmation": []},
        ]
        return g
    model = ControlledModel(extraction(), partially_valid)
    result = run(case, model)
    assert result.status == "partial" and result.error_code is None and len(model.calls) == 2
    assert result.guidance.actions and len(result.guidance.rewrites) == 1
    assert result.omitted_components[0].index == 1
    assert "Kafka" not in result.model_dump_json() and "80%" not in render.present(result)
    assert "可选改写未通过" in render.present(result) and "可选改写未通过" in render.render_fallback(result)
    assert case.store.result(result.id, case.user, "thread", "file") == result
    with pytest.raises(ValidationError):
        Result.model_validate({**result.model_dump(), "status": "success"})


def test_safe_rewrite_and_confirmed_answer_flow(case):
    initial = run(case, ControlledModel(extraction(), guidance))
    answer_text = "尚未使用 Redis。"

    def updated(payload):
        assert payload["previous_guidance"] is not None
        user_source = next(s for s in payload["sources"] if s["kind"] == "user")
        assert user_source["text"] == answer_text and user_source["requirement_id"] == "r2"
        g = guidance(payload)
        g["matches"][1].update(status="confirmed_gap", evidence_source_ids=[user_source["id"]],
                                observation="用户明确确认尚未使用", rationale="用户陈述，并非根据简历遗漏推断")
        g["questions"] = []
        g["rewrites"] = [{"source_id": payload["sources"][0]["id"], "sentences": [TEXT.splitlines()[0]],
                          "supporting_source_ids": [], "reason": "保留真实职责，不添加虚假指标",
                          "pending_confirmation": ["是否有可复现的性能验证？"]}]
        return g

    result = run(case, ControlledModel(updated), parent_id=initial.id,
                 answers=[{"question_id": "q1", "text": answer_text, "confirms_absence": True}])
    assert result.status == "success" and result.guidance.matches[1].status == "confirmed_gap"
    assert result.guidance.rewrites[0].sentences == [TEXT.splitlines()[0]]
    assert all(s.kind != "user" for s in initial.sources)


def test_confirmed_user_fact_can_enter_extractively_rewritten_version(case):
    first = run(case, ControlledModel(extraction(), guidance))
    statement = "在该订单 API 中使用 Redis 缓存商品查询结果。"
    def updated(payload):
        user = next(s for s in payload["sources"] if s["kind"] == "user")
        g = guidance(payload)
        g["matches"][1].update(status="evidenced", evidence_source_ids=[user["id"]])
        g["questions"] = []
        g["rewrites"] = [{"source_id": source_units(TEXT, "resume")[0].id,
                          "sentences": [TEXT.splitlines()[0], statement], "supporting_source_ids": [user["id"]],
                          "reason": "合入用户确认的同一项目用途，不推测指标", "pending_confirmation": []}]
        return g
    model = ControlledModel(updated)
    result = run(case, model, parent_id=first.id,
                 answers=[{"question_id": "q1", "text": statement, "confirms_absence": False}])
    assert result.status == "success" and len(model.calls) == 1
    assert result.guidance.rewrites[0].sentences[-1] == statement
    assert case.store.owned_file(case.user, "thread", "file")["extracted_text"] == TEXT


def test_wrong_question_file_user_and_target_are_rejected_before_model(case):
    first = run(case, ControlledModel(extraction(), guidance))
    model = ControlledModel()
    with pytest.raises(HTTPException) as e:
        run(case, model, parent_id=first.id, answers=[{"question_id": "wrong", "text": "answer"}])
    assert e.value.status_code == 422
    case.api.db.save_file_meta("file2", "second.pdf", 10, "thread", case.user, extracted_text=TEXT)
    for req, user in [
        (AnalyzeRequest(thread_id="thread", file_id="file2", target_role="后端开发", jd=JD, parent_id=first.id), case.user),
        (AnalyzeRequest(thread_id="thread", file_id="file", target_role="后端开发", jd=JD, parent_id=first.id), "intruder"),
        (AnalyzeRequest(thread_id="other-thread", file_id="file", parent_id=first.id), case.user),
        (AnalyzeRequest(thread_id="thread", file_id="file", target_role="前端", jd=JD, parent_id=first.id), case.user),
    ]:
        with pytest.raises(HTTPException):
            analyze(req, user, case.store, lambda: model)
    assert model.calls == []


@pytest.mark.parametrize("error,code", [(TimeoutError(), "timeout"), (ConnectionError(), "network")])
def test_retry_exhaustion_and_cache_survive(case, error, code):
    model = ControlledModel(extraction(), error, error)
    request = AnalyzeRequest(thread_id="thread", file_id="file", target_role="后端开发", jd=JD)
    result = analyze(request, case.user, case.store, lambda: model, Budget(attempts=2))
    assert result.status == "partial" and result.error_code == code and result.guidance is None
    assert len(model.calls) == 3
    assert case.store.extraction("file", TEXT) is not None
    assert run(case, ControlledModel(guidance)).guidance is not None


def test_transient_and_invalid_output_retry_only_current_stage(case):
    class RateLimitError(Exception):
        status_code = 429
    model = ControlledModel(RateLimitError(), extraction(), "invalid", guidance)
    request = AnalyzeRequest(thread_id="thread", file_id="file", target_role="后端开发", jd=JD)
    result = analyze(request, case.user, case.store, lambda: model, Budget(attempts=2))
    assert result.error_code is None and len(model.calls) == 4
    assert json.loads(model.calls[2][0][-1].content) == json.loads(model.calls[3][0][-1].content)


def test_budget_exhaustion_cannot_be_success(case, monkeypatch):
    import agent.nodes as nodes
    clock = [0.0]
    monkeypatch.setattr(nodes.time, "monotonic", lambda: clock[0])
    def slow(payload):
        clock[0] = 11
        return extraction()
    budget = Budget(seconds=10)
    result = analyze(AnalyzeRequest(thread_id="thread", file_id="file", target_role="后端"),
                     case.user, case.store, lambda: ControlledModel(slow), budget)
    assert result.status == "failed" and result.error_code == "budget_exhausted"
    assert case.store.extraction("file", TEXT) is None


def test_display_fallback_retains_validated_core(case, monkeypatch):
    from agent import render
    result = run(case, ControlledModel(extraction(), guidance))
    normal = render.present(result)
    assert result.guidance.actions[0].problem in normal and "暂无足够信息" in normal
    def fail(_):
        raise RuntimeError("renderer unavailable")
    monkeypatch.setattr(render, "render_markdown", fail)
    fallback = render.present(result)
    assert result.guidance.actions[0].next_step in fallback and "基础展示" in fallback
    assert result.guidance.actions[0].rationale in fallback


def test_production_schema_is_the_eval_and_prompt_contract():
    from eval.schemas import Guidance as EvalGuidance
    from agent.nodes import _load_prompt
    assert EvalGuidance is Guidance
    assert "basis=jd" in _load_prompt("guide.md")
    model = ControlledModel(guidance)
    payload = {"sources": [s.model_dump() for s in source_units(TEXT, "resume")]}
    checked = call_structured(model, "guide.md", payload, Guidance,
                              lambda g: validate_guidance(g, source_units(TEXT, "resume")), Budget(attempts=1))
    prompt = model.calls[0][0][0].content
    schema = json.loads(prompt.split("生产 JSON Schema（唯一协议）：\n")[1])
    assert schema == Guidance.model_json_schema()
    assert checked.actions and "score" not in schema["properties"]


def test_post_route_and_history_use_same_result_schema(case, monkeypatch):
    from agent import guidance_service
    model = ControlledModel(extraction(), guidance)
    monkeypatch.setattr(guidance_service, "new_model", lambda: model)
    response = case.api.client.post("/api/resume-guidance", json={"thread_id": "thread", "file_id": "file", "target_role": "后端开发", "jd": JD})
    assert response.status_code == 200, response.text
    data = response.json()
    result = Result.model_validate(data["result"])
    history = case.api.client.get("/api/conversations/thread/messages").json()
    saved = json.loads(history[-1]["search_info"])["guidance"]
    assert saved == result.model_dump() and history[-1]["content"] == data["markdown"]
    assert history[-2]["file"]["id"] == "file"
    assert case.api.client.get(f"/api/resume-guidance/file/{result.id}", params={"thread_id": "thread"}).json() == saved
    from fastapi.testclient import TestClient
    with TestClient(case.api.app) as other:
        signup(other, "other@example.com")
        assert other.get(f"/api/resume-guidance/file/{result.id}", params={"thread_id": "thread"}).status_code == 404
        assert other.post("/api/resume-guidance", json={"thread_id": "thread", "file_id": "file", "target_role": "后端开发", "jd": JD,
                                                        "parent_id": result.id, "answers": [{"question_id": "q1", "text": "fake"}]}).status_code == 404
    assert len(model.calls) == 2


def test_non_retryable_model_error_and_concurrency_limit(case, monkeypatch):
    result = run(case, ControlledModel(PermissionError("do not leak secret")))
    assert result.status == "failed" and result.error_code == "model_error"
    from agent import guidance_service
    assert guidance_service._WORKERS.acquire(False)
    assert guidance_service._WORKERS.acquire(False)
    try:
        with pytest.raises(HTTPException) as error:
            guidance_service.run_guidance(AnalyzeRequest(thread_id="thread", file_id="file"), case.user)
        assert error.value.status_code == 429
    finally:
        guidance_service._WORKERS.release()
        guidance_service._WORKERS.release()


def test_sdk_retries_disabled_and_timeout_configurable(monkeypatch):
    chat_models = pytest.importorskip("langchain.chat_models")
    from agent.guidance_service import new_model
    seen = {}
    def factory(**kwargs):
        seen.update(kwargs)
        return object()
    monkeypatch.setattr(chat_models, "init_chat_model", factory)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-dummy")
    monkeypatch.setenv("GUIDANCE_REQUEST_TIMEOUT_SECONDS", "7")
    new_model()
    assert seen["max_retries"] == 0 and seen["timeout"] == 7
    assert seen["model_provider"] == "deepseek"
    assert seen["model_kwargs"]["response_format"] == {"type": "json_object"}
    assert seen["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize("kind,expected", [("APITimeoutError", "timeout"), ("APIConnectionError", "network")])
def test_wrapped_sdk_exceptions_remain_retryable(case, kind, expected):
    # 无 SDK 的 CI 也能验证子类分类；真实 SDK/HTTP 的包装另有集成测试。
    base = type(kind, (Exception,), {})
    wrapped = type("FutureSDKWrappedError", (base,), {})
    model = ControlledModel(wrapped("private provider text"), extraction(), guidance)
    request = AnalyzeRequest(thread_id="thread", file_id="file", target_role="后端开发", jd=JD)
    result = analyze(request, case.user, case.store, lambda: model, Budget(attempts=2))
    assert result.error_code is None and result.guidance and len(model.calls) == 3
    from agent.nodes import classify_error
    assert classify_error(wrapped()) == (expected, True)


@pytest.mark.parametrize("original,quote", [
    ("计算机\u00a0本科\u00a02024-2028", "计算机 本科 2024-2028"),
    ("项目：Python\u202fAPI\t接口。", "Python API 接口"),
])
def test_pdf_whitespace_quotes_restore_exact_original_slice(original, quote):
    from agent.nodes import validate_extraction
    from agent.schema import Extraction
    sources = source_units(original, "resume")
    value = Extraction(facts=[{"kind": "other", "source_id": sources[0].id, "quote": quote}])
    validate_extraction(value, sources)
    assert value.facts[0].quote in original
    assert value.facts[0].source_id == sources[0].id
    assert value.facts[0].quote != quote


@pytest.mark.parametrize("quote,source_id", [("用 Kafka 实现 API", None), ("用Python实现API", None),
                                           ("用 Python 实现 API", "resume:unknown")])
def test_quote_alignment_does_not_accept_text_changes_or_unknown_sources(quote, source_id):
    from agent.nodes import validate_extraction
    from agent.schema import Extraction
    sources = source_units("用\u00a0Python\u00a0实现 API。", "resume")
    value = Extraction(facts=[{"kind": "project", "source_id": source_id or sources[0].id, "quote": quote}])
    with pytest.raises(ValueError, match="invalid fact citation"):
        validate_extraction(value, sources)


def test_whitespace_answers_and_false_complete_status_rejected():
    with pytest.raises(ValidationError):
        AnalyzeRequest(thread_id="t", file_id="f", answers=[{"question_id": "q", "text": "  "}])
    with pytest.raises(ValidationError):
        Result(id="id", file_id="f", target_role="后端", jd_used=False, status="success", sources=[],
               facts=[], guidance=None, limitations=[])


def test_old_guidance_survives_failed_followup_without_false_success(case):
    first = run(case, ControlledModel(extraction(), guidance))
    result = run(case, ControlledModel(TimeoutError()), parent_id=first.id,
                 answers=[{"question_id": "q1", "text": "未使用过 Redis", "confirms_absence": True}])
    assert result.status == "partial" and result.error_code == "timeout"
    assert result.guidance == first.guidance
    assert any("尚未结合本轮补充更新" in x for x in result.limitations)
    assert any(s.kind == "user" for s in result.sources)


def test_false_gap_cannot_use_another_requirement_confirmation(case):
    first = run(case, ControlledModel(extraction(), guidance))
    def bad(payload):
        g = guidance(payload)
        user = next(s for s in payload["sources"] if s["kind"] == "user")
        g["matches"][0].update(status="confirmed_gap", evidence_source_ids=[user["id"]])
        return g
    result = run(case, ControlledModel(bad), parent_id=first.id,
                 answers=[{"question_id": "q1", "text": "未使用 Redis", "confirms_absence": True}])
    assert result.error_code == "invalid_output" and result.status == "partial"
    assert result.guidance == first.guidance  # 不保存错误更新


def test_cache_corruption_and_missing_text_are_processing_failures(case):
    first = run(case, ControlledModel(extraction(), guidance))
    with case.api.db.get_app_db() as conn:
        conn.execute("UPDATE guidance_extractions SET extraction = '{}' WHERE file_id = 'file'")
    result = run(case, ControlledModel())
    assert result.status == "failed" and result.error_code == "invalid_cached_extraction"
    with case.api.db.get_app_db() as conn:
        conn.execute("UPDATE uploaded_files SET extracted_text = '' WHERE file_id = 'file'")
    result = run(case, ControlledModel())
    assert result.status == "failed" and result.error_code == "text_unavailable"
    assert result.guidance is None


def test_stale_text_cannot_receive_old_answers(case):
    first = run(case, ControlledModel(extraction(), guidance))
    with case.api.db.get_app_db() as conn:
        conn.execute("UPDATE uploaded_files SET extracted_text = '另一份解析文本' WHERE file_id = 'file'")
    model = ControlledModel()
    with pytest.raises(HTTPException) as error:
        run(case, model, parent_id=first.id, answers=[{"question_id": "q1", "text": "未使用 Redis"}])
    assert error.value.status_code == 409 and model.calls == []


def test_cached_failure_with_previous_core_is_partial(case):
    first = run(case, ControlledModel(extraction(), guidance))
    with case.api.db.get_app_db() as conn:
        conn.execute("UPDATE guidance_extractions SET extraction = '{}' WHERE file_id = 'file'")
    result = run(case, ControlledModel(), parent_id=first.id,
                 answers=[{"question_id": "q1", "text": "未使用 Redis", "confirms_absence": True}])
    assert result.status == "partial" and result.error_code == "invalid_cached_extraction"
    assert result.guidance == first.guidance


def test_file_deletion_cascades_guidance_and_extraction(case):
    run(case, ControlledModel(extraction(), guidance))
    assert case.api.client.delete("/api/files/file").status_code == 200
    with case.api.db.get_app_db() as conn:
        assert conn.execute("SELECT count(*) FROM guidance_results").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM guidance_extractions").fetchone()[0] == 0


def test_render_escapes_source_html_and_no_partial_chunks(case):
    from agent.render import present
    model = ControlledModel(extraction(), guidance)
    states = []
    request = AnalyzeRequest(thread_id="thread", file_id="file", target_role="<script>alert(1)</script>", jd=JD)
    result = analyze(request, case.user, case.store, lambda: model, Budget(attempts=1), states.append)
    assert states == ["提取简历事实中", "对齐岗位要求与证据中"]
    assert "<script>" not in present(result) and "&lt;script" in present(result)
