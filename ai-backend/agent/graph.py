"""核心指导链路：提取（可复用）→ 岗位确认 → 一次证据分析 → 校验。

保留模块路径，不再为评分展示编排并行 Agent；持久化由文件级 store 完成。
"""
import hashlib
import uuid

from fastapi import HTTPException

from agent.nodes import (Budget, ProcessingError, call_structured, failure_detail,
                         filter_optional_rewrites, validate_extraction)
from agent.schema import AnalyzeRequest, Extraction, Guidance, Result, Source

BASE_LIMITATIONS = [
    "来源引用仅表示文本存在，不表示经历经过独立核实；模型判断可能有误，请核对。",
    "未提及不等于不会；学历仅在目标要求明确时讨论，不按学校名气评价。",
    "仅分析提取文本，无法据此判断完整视觉排版；不预测录用结果。",
]


def source_units(text, kind):
    # 已有 PDF/OCR 文本按非空行切片。ID 由内容和重复序号生成，跨轮稳定。
    seen = {}
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        digest = hashlib.sha256(line.encode()).hexdigest()[:16]
        seen[digest] = seen.get(digest, 0) + 1
        result.append(Source(id=f"{kind}:{digest}:{seen[digest]}", kind=kind, text=line))
    return result


def analyze(request: AnalyzeRequest, user_id, store, model_factory, budget=None, emit=None):
    # 所有权检查在读取缓存、补充陈述或创建模型之前执行。
    row = store.owned_file(user_id, request.thread_id, request.file_id)
    parent = store.result(request.parent_id, user_id, request.thread_id, request.file_id) if request.parent_id else None
    if request.answers and (not parent or not parent.guidance):
        raise HTTPException(422, "补充回答必须绑定有追问的分析记录")
    target_changed = parent and parent.target_role and (
        request.target_role != parent.target_role
        or [s.text for s in source_units(request.jd, "jd")] != [s.text for s in parent.sources if s.kind == "jd"]
    )
    if target_changed:
        raise HTTPException(422, "续答需保留原岗位与 JD；更换目标请发起新分析")
    old_questions = {q.id: q for q in parent.guidance.questions} if parent and parent.guidance else {}
    if (len({a.question_id for a in request.answers}) != len(request.answers)
            or any(a.question_id not in old_questions for a in request.answers)):
        raise HTTPException(422, "回答的问题不属于此分析记录")
    text = row["extracted_text"] or ""
    resume_sources = source_units(text, "resume")
    if parent and ([(s.id, s.text) for s in resume_sources]
                   != [(s.id, s.text) for s in parent.sources if s.kind == "resume"]):
        raise HTTPException(409, "简历提取文本已变化，请重新分析，不能继续旧问题")
    sources = resume_sources + source_units(request.jd.strip(), "jd")
    if parent:
        sources.extend(s for s in parent.sources if s.kind == "user")
    result_id = uuid.uuid4().hex
    for i, a in enumerate(request.answers):
        sources.append(Source(id=f"user:{result_id}:{i + 1}", kind="user", text=a.text.strip(),
                              confirms_absence=a.confirms_absence,
                              requirement_id=old_questions[a.question_id].requirement_id))
    result = Result(id=result_id, file_id=request.file_id, target_role=request.target_role.strip(),
                    jd_used=bool(request.jd.strip()), status="failed", sources=sources, facts=[],
                    guidance=None, limitations=list(BASE_LIMITATIONS))
    if not result.jd_used:
        result.limitations.append("未提供 JD：依据岗位的一般要求分析，不代表具体企业招聘标准。")
    budget = budget or Budget()
    model = None

    def get_model():
        nonlocal model
        budget.remaining()
        if model is None:
            try:
                model = model_factory()
            except ProcessingError:
                raise
            except Exception as exc:
                raise ProcessingError("model_unavailable", failure_detail(exc, "model_init", 0)) from exc
        return model

    try:
        if not text.strip():
            raise ProcessingError("text_unavailable")
        try:
            extraction = store.extraction(request.file_id, text)
            if extraction:
                validate_extraction(extraction, sources)
        except ValueError as exc:
            raise ProcessingError("invalid_cached_extraction") from exc
        if not extraction:
            if emit:
                emit("提取简历事实中")
            extraction = call_structured(get_model(), "extract.md",
                                         {"sources": [s.model_dump() for s in sources if s.kind == "resume"]},
                                         Extraction, lambda x: validate_extraction(x, sources), budget)
            store.save_extraction(request.file_id, text, extraction)
        result.facts = extraction.facts
        if not result.target_role:
            result.status = "needs_target"
            result.limitations.append("请由用户确认目标岗位；不根据简历或模型猜测自动确定。")
        else:
            if emit:
                emit("对齐岗位要求与证据中")

            def validate(g):
                if parent and parent.guidance:
                    old = {m.id: (m.requirement, m.basis, m.requirement_source_ids) for m in parent.guidance.matches}
                    new = {m.id: (m.requirement, m.basis, m.requirement_source_ids) for m in g.matches}
                    if old != new:
                        raise ValueError("follow-up must keep requirement identities")
                result.omitted_components = filter_optional_rewrites(g, sources)

            result.guidance = call_structured(get_model(), "guide.md", {
                "target_role_confirmed_by_user": result.target_role,
                "jd_used": result.jd_used,
                "sources": [s.model_dump() for s in sources],
                "facts": [f.model_dump() for f in extraction.facts],
                "previous_guidance": parent.guidance.model_dump() if parent and parent.guidance else None,
            }, Guidance, validate, budget)
            result.guidance.actions.sort(key=lambda a: a.priority)
            incomplete = (result.omitted_components or result.guidance.questions
                          or any(m.status == "insufficient" for m in result.guidance.matches))
            result.status = "partial" if incomplete else "success"
    except ProcessingError as exc:
        result.error_code = exc.code
        result.processing_error = exc.detail
        result.status = "partial" if result.facts else "failed"
        result.limitations.append("本轮模型处理未完成，不应将处理失败理解为缺少能力或经历。")
        # 可复用上一轮已校验指导，但明确不是本轮更新成功。
        if parent and parent.guidance:
            result.status = "partial"
            result.guidance = parent.guidance
            result.omitted_components = parent.omitted_components
            result.limitations.append("以下核心指导沿用上一轮已校验结果，尚未结合本轮补充更新。")
    store.save_result(result)
    return result
