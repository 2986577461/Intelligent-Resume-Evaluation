"""有界模型调用与确定性校验；没有评分节点、隐式 raw 回退或模型报告流。"""
import json
import logging
import os
import re
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from agent.schema import Extraction, Guidance, OmittedComponent, ProcessingFailure, Source

logger = logging.getLogger(__name__)


class ProcessingError(Exception):
    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail
        super().__init__(code)


def failure_detail(exc, stage, attempts):
    # 严禁 str(exc)/exc_info：SDK 异常正文可能携带请求内容、地址、密钥。
    name = re.sub(r"[^a-zA-Z0-9_]", "", type(exc).__name__)[:80]
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return ProcessingFailure(stage=stage, exception_type=name, http_status=status if type(status) is int else None,
                             attempts=attempts)


class Budget:
    def __init__(self, seconds=None, request_timeout=None, attempts=None):
        self.deadline = time.monotonic() + (seconds if seconds is not None else float(os.getenv("GUIDANCE_BUDGET_SECONDS", "90")))
        self.request_timeout = request_timeout if request_timeout is not None else float(os.getenv("GUIDANCE_REQUEST_TIMEOUT_SECONDS", "25"))
        self.attempts = attempts if attempts is not None else int(os.getenv("GUIDANCE_MAX_ATTEMPTS", "2"))
        self.attempts = max(1, min(self.attempts, 3))

    def remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise ProcessingError("budget_exhausted")
        return left


def classify_error(exc):
    # 新版 LangChain 将 OpenAI 异常包装为 OpenAITimeoutError 等子类。
    # 按继承关系识别，不能仅比较最外层类型名称，否则超时/断网不会重试。
    names = {base.__name__ for base in type(exc).__mro__}
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, TimeoutError) or names & {"APITimeoutError", "ModelTimeoutError", "TimeoutException", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout"}:
        return "timeout", True
    if status == 429 or names & {"RateLimitError", "ModelRateLimitError"}:
        return "rate_limit", True
    if isinstance(exc, ConnectionError) or names & {"APIConnectionError", "ModelConnectionError", "ConnectError", "ReadError", "WriteError", "RemoteProtocolError"} or (status and status >= 500):
        return "network", True
    if status in {400, 422}:
        return "request_rejected", False
    if status == 401:
        return "authentication", False
    if status == 402:
        return "quota", False
    if status == 403:
        return "permission", False
    if status == 404:
        return "model_not_found", False
    if status == 413:
        return "input_too_large", False
    if isinstance(exc, (ValueError, ValidationError, json.JSONDecodeError)):
        return "invalid_output", True
    return "model_error", False


def _load_prompt(name):
    return (Path(__file__).parent / "prompts" / name).read_text(encoding="utf-8").strip()


def call_structured(model, prompt_name, payload, schema, validate, budget):
    prompt = _load_prompt(prompt_name) + "\n生产 JSON Schema（唯一协议）：\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    for attempt in range(budget.attempts):
        timeout = min(budget.request_timeout, budget.remaining())
        try:
            # SDK 重试必须关闭。不是 Future.result(timeout)，不会把线程等待误认成取消。
            response = model.invoke(
                [SystemMessage(content=prompt), HumanMessage(content=json.dumps(payload, ensure_ascii=False))],
                timeout=timeout,
                config={"callbacks": [], "run_name": "guidance_internal", "tags": ["guidance_internal"]},
            )
            budget.remaining()  # 晚到结果不冒充预算内成功。
            if not isinstance(response.content, str):
                raise ValueError("structured response must be text")
            content = response.content.strip()
            if content.startswith("```") and content.endswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = schema.model_validate_json(content)
            validate(result)
            return result
        except ProcessingError:
            raise
        except Exception as exc:
            code, retryable = classify_error(exc)
            detail = failure_detail(exc, "extract" if prompt_name == "extract.md" else "guide", attempt + 1)
            logger.warning("guidance_call_failed code=%s stage=%s exception=%s http_status=%s attempt=%s",
                           code, detail.stage, detail.exception_type, detail.http_status, attempt + 1)
            if not retryable or attempt + 1 == budget.attempts:
                raise ProcessingError(code, detail) from exc
            delay = min(0.25 * (2 ** attempt), budget.remaining())
            time.sleep(delay)
    raise ProcessingError("model_error")


def validate_extraction(extraction: Extraction, sources: list[Source]):
    by_id = {s.id: s for s in sources if s.kind == "resume"}
    for fact in extraction.facts:
        source = by_id.get(fact.source_id)
        if source is None:
            raise ValueError("invalid fact citation")
        if fact.quote not in source.text:
            # PDF 常含 NBSP 等 Unicode 空白，模型可能输出普通空格。
            # 只允许空白等价对齐，再将摘录恢复成原文的精确切片；不做模糊/语义匹配。
            parts = re.split(r"\s+", fact.quote.strip())
            match = re.search(r"\s+".join(re.escape(p) for p in parts), source.text) if any(parts) else None
            if match is None:
                raise ValueError("invalid fact citation")
            fact.quote = source.text[match.start():match.end()]


def sentences(text):
    # 整句而非任意子串，防止把“未使用 Redis”裁成“使用 Redis”。
    return [s.strip() for s in re.split(r"(?<=[。！？；.!?;])\s*|\n+", text) if s.strip()]


def filter_optional_rewrites(g: Guidance, sources: list[Source]):
    # 核心内容任何校验失败都仍然拒绝整轮；可选改写不能拖垮已校验核心。
    core = g.model_copy(update={"rewrites": []})
    validate_guidance(core, sources)
    accepted, omitted = [], []
    for index, rewrite in enumerate(g.rewrites):
        try:
            validate_guidance(core.model_copy(update={"rewrites": [rewrite]}), sources)
        except ValueError:
            omitted.append(OmittedComponent(index=index))
        else:
            accepted.append(rewrite)
    g.rewrites = accepted
    return omitted


def validate_guidance(g: Guidance, sources: list[Source]):
    by_id = {s.id: s for s in sources}

    def refs(ids, kinds=None):
        if len(ids) != len(set(ids)) or any(i not in by_id or (kinds and by_id[i].kind not in kinds) for i in ids):
            raise ValueError("invalid citation")

    matches = {m.id: m for m in g.matches}
    if len(matches) != len(g.matches) or any(not m.id.strip() for m in g.matches):
        raise ValueError("duplicate/empty requirement id")
    for m in g.matches:
        refs(m.requirement_source_ids, {"jd"})
        refs(m.evidence_source_ids, {"resume", "user"})
        if m.basis == "jd":
            if not m.requirement_source_ids or not any(m.requirement in by_id[i].text for i in m.requirement_source_ids):
                raise ValueError("JD requirement must quote JD")
        elif m.requirement_source_ids:
            raise ValueError("general advice cannot claim JD provenance")
        if m.status == "evidenced" and (not m.evidence_source_ids or any(by_id[i].confirms_absence for i in m.evidence_source_ids)):
            raise ValueError("evidence required")
        if m.status == "confirmed_gap" and not any(by_id[i].kind == "user" and by_id[i].confirms_absence and by_id[i].requirement_id == m.id for i in m.evidence_source_ids):
            raise ValueError("gap needs explicit user confirmation for this requirement")
    for a in g.actions:
        refs(a.source_ids, {"resume", "user"})
        if a.requirement_id not in matches:
            raise ValueError("invalid action requirement")
    qids = set()
    for q in g.questions:
        if q.requirement_id not in matches or not q.id.strip() or q.id in qids:
            raise ValueError("invalid question")
        qids.add(q.id)
    for r in g.rewrites:
        refs([r.source_id], {"resume"})
        refs(r.supporting_source_ids, {"resume", "user"})
        # 不混入另一项目的简历段落。补充陈述仍需用户核对适用范围。
        allowed = [by_id[r.source_id]]
        for sid in r.supporting_source_ids:
            src = by_id[sid]
            if src.kind != "user" or src.confirms_absence:
                raise ValueError("rewrite support must be affirmative user statement")
            allowed.append(src)
        pool = {sentence for s in allowed for sentence in sentences(s.text)}
        if any(sentence not in pool for sentence in r.sentences):
            raise ValueError("rewrite introduces unconfirmed text")
