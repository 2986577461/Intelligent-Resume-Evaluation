"""评分工具入口：可复用提取缓存，节点性能与首个有效正文计时。"""

import hashlib
import json
import os

from agent.graph import build_extraction_graph, build_scoring_graph
from agent.nodes import _load_prompt
from agent.schema import Extraction
from agent.telemetry import EvaluationTrace
from state.manager import CacheManager


_CACHE_PROTOCOL = "extraction.v1"


def create_scoring_model():
    from langchain.chat_models import init_chat_model

    name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    options = dict(max_tokens=4096, timeout=float(os.getenv("SCORING_REQUEST_TIMEOUT_SECONDS", "60")),
                   max_retries=0)
    if name.startswith("deepseek"):
        base = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("DEEPSEEK_API_BASE")
        if base:
            options["api_base"] = base
        # 结构化评分不依赖隐藏思考，避免每个节点都产生额外 reasoning token。
        options["extra_body"] = {"thinking": {"type": "disabled"}}
    return init_chat_model(model=name, **options).bind(response_format={"type": "json_object"})


def _extraction_version():
    raw = json.dumps([_CACHE_PROTOCOL, _load_prompt("resume_analyst.md"),
                      Extraction.model_json_schema(), os.getenv("MODEL_NAME", "deepseek-v4-flash"),
                      os.getenv("DEEPSEEK_BASE_URL") or os.getenv("DEEPSEEK_API_BASE", "")],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def evaluate_resume(data, user_id, thread_id, model, cache=None, position=None,
                    emit_state=None, emit_chunk=None, trace=None):
    trace = trace or EvaluationTrace()
    # 工具只在全部分析校验成功后发送正文，失败时不展示未完成的评分表。
    def output(text):
        if text:
            trace.content()
            if emit_chunk:
                emit_chunk(text)

    try:
        key = CacheManager.extraction_key(user_id, thread_id, data["file_id"],
                                          data["text"], _extraction_version())
        extraction = None
        if cache:
            try:
                cached = cache.get_extraction(key)
                if cached is not None:
                    extraction = Extraction.model_validate(cached).model_dump()
            except Exception as error:
                # Redis 不可用/旧缓存损坏不能阻止分析；不记录异常正文或缓存内容。
                trace.record("cache_error", operation="read", error_type=type(error).__name__)
                cache = None
        trace.record("cache", hit=extraction is not None)
        if extraction is None:
            result = build_extraction_graph(model, emit_state, trace).invoke({"resume_text": data["text"]})
            extraction = Extraction.model_validate({key: result[key] for key in Extraction.model_fields}).model_dump()
            if cache:
                try:
                    cache.set_extraction(key, extraction)
                except Exception as error:
                    trace.record("cache_error", operation="write", error_type=type(error).__name__)
        elif emit_state:
            emit_state("已复用简历提取结果")

        # 覆盖目标只作用于本轮，不把用户补充的岗位写入可复用事实缓存。
        if position and position.strip():
            extraction["position"] = position.strip()
        if not extraction.get("position"):
            trace.finish("needs_position")
            return "简历上没有岗位信息，你想投递什么岗位？"

        extraction["page_count"] = data["page_count"]
        # 摘要未覆盖的可实习周期、奖项等仍能在综合阶段核对，不能把漏提取当作原文没写。
        extraction["resume_text"] = data["text"]
        if emit_state:
            emit_state("简历信息评估中")
        result = build_scoring_graph(model, emit_state, output, trace).invoke(extraction)
        trace.finish("success")
        return result["report"]
    except Exception as error:
        trace.record("failure", error_type=type(error).__name__)
        trace.finish("failed")
        return "简历分析失败，请稍后重试。"
