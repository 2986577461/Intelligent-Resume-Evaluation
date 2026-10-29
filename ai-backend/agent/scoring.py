"""简历指导入口。保留模块名兼容现有工具，不再生成评分。"""

import hashlib
import json
import os

from agent.graph import build_context_graph, build_extraction_graph, build_scoring_graph
from agent.nodes import _load_prompt
from agent.report_stream import ReportStreamInterrupted
from agent.schema import Extraction, GuidanceContext
from agent.telemetry import EvaluationTrace
from state.manager import CacheManager

_CACHE_PROTOCOL = "extraction.guidance.v2"


def create_scoring_model():
    from langchain.chat_models import init_chat_model

    name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    options = dict(max_tokens=8192, timeout=float(os.getenv("SCORING_REQUEST_TIMEOUT_SECONDS", "60")),
                   max_retries=0)
    if name.startswith("deepseek"):
        base = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("DEEPSEEK_API_BASE")
        if base:
            options["api_base"] = base
        options["extra_body"] = {"thinking": {"type": "disabled"}}
    return init_chat_model(model=name, **options).bind(response_format={"type": "json_object"})


def _extraction_version():
    raw = json.dumps([_CACHE_PROTOCOL, _load_prompt("resume_analyst.md"),
                      Extraction.model_json_schema(), os.getenv("MODEL_NAME", "deepseek-v4-flash"),
                      os.getenv("DEEPSEEK_BASE_URL") or os.getenv("DEEPSEEK_API_BASE", "")],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def clarification(context):
    missing = context.missing_fields()
    if missing == ["position", "application_type"]:
        question = "你这次想投递什么岗位？是实习、校招还是社招？"
    elif missing == ["position"]:
        question = "你这次想投递什么岗位？"
    else:
        question = "你这次是投实习、校招还是社招？"
    return "简历已解析，已暂存解析结果和已确认的投递信息。" + question


def evaluate_resume(data, user_id, thread_id, model, cache=None, *, user_message="",
                    store=None, emit_state=None, emit_chunk=None, trace=None):
    trace = trace or EvaluationTrace()

    def output(text):
        if text:
            trace.content()
            if emit_chunk:
                emit_chunk(text)

    try:
        snapshot = store.load(user_id, thread_id) if store else None
        if snapshot and snapshot["file_id"] != data["file_id"]:
            snapshot = None  # 新附件不能继承另一份简历的投递目标。
        key = CacheManager.extraction_key(user_id, thread_id, data["file_id"],
                                          data["text"], _extraction_version())
        extraction = None
        if snapshot and snapshot["extraction_key"] == key:
            extraction = Extraction.model_validate(snapshot["extraction"]).model_dump()
            trace.record("extraction_reuse", source="local_state")
        if extraction is None and cache:
            try:
                cached = cache.get_extraction(key)
                if cached is not None:
                    extraction = Extraction.model_validate(cached).model_dump()
            except Exception as error:
                trace.record("cache_error", operation="read", error_type=type(error).__name__)
                cache = None
        trace.record("cache", hit=extraction is not None)
        if extraction is None:
            result = build_extraction_graph(model, emit_state, trace).invoke({"resume_text": data["text"]})
            extraction = Extraction.model_validate({field: result[field] for field in Extraction.model_fields}).model_dump()
            if cache:
                try:
                    cache.set_extraction(key, extraction)
                except Exception as error:
                    trace.record("cache_error", operation="write", error_type=type(error).__name__)
        elif emit_state:
            emit_state("已复用简历提取结果")

        # 先保存解析结果，再识别上下文；后者失败也不必重新解析。
        previous = GuidanceContext.model_validate(snapshot["context"] if snapshot else {}).model_dump()
        if store:
            store.save(user_id, thread_id, data["file_id"], key, extraction, previous, "waiting")
        if emit_state:
            emit_state("识别投递目标中")
        context = build_context_graph(model, trace).invoke({
            "resume_text": data["text"], "position": extraction["position"],
            "user_message": user_message, "previous_context": previous,
        })["context"]
        context = GuidanceContext.model_validate(context)
        if store:
            store.save(user_id, thread_id, data["file_id"], key, extraction, context.model_dump(),
                       "waiting" if context.missing_fields() else "ready")
        if context.missing_fields():
            trace.finish("needs_context")
            # 无持久化 store 的独立调用不能谎称已保存状态。
            return clarification(context) if store else clarification(context).replace(
                "，已暂存解析结果和已确认的投递信息", "")

        state = {**extraction, "page_count": data["page_count"], "resume_text": data["text"],
                 "context": context.model_dump()}
        result = build_scoring_graph(model, emit_state, output, trace).invoke(state)
        trace.finish("success")
        return result["report"]
    except ReportStreamInterrupted as error:
        trace.record("failure", error_type=type(error.__cause__).__name__)
        trace.finish("incomplete")
        # 与已推送内容完全一致，外层不会再追加/重复整份报告。
        return error.report
    except Exception as error:
        trace.record("failure", error_type=type(error).__name__)
        trace.finish("failed")
        return "简历指导生成失败，请稍后重试。"
