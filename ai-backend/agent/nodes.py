"""事实提取、投递上下文识别、单次综合指导和确定性渲染。"""

import json
import os
from contextlib import closing
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage

from agent.report_stream import (INTERRUPTED, ReportJSONStream, ReportStreamInterrupted,
                                 ReportStreamRenderer, render_report)
from agent.resume_context import normalize_text, resume_target_hints
from agent.schema import ContextResolution, ContextSignal, Extraction, GuidanceContext, Report, Synthesis

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_prompt(name):
    with open(os.path.join(SCRIPT_DIR, "prompts", name), encoding="utf-8") as file:
        return file.read().strip()


def _call_llm(prompt, text, model, trace=None, stage="internal"):
    response = model.invoke(
        [SystemMessage(content=prompt), HumanMessage(content=text)],
        config={"callbacks": [], "run_name": f"resume_{stage}", "tags": ["resume_guidance_internal"]},
    )
    if trace:
        trace.usage(stage, response)
    content = response.content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("模型必须返回 JSON 对象")
    return result


def _structured_call(prompt_name, payload, schema, model, trace, stage):
    prompt = _load_prompt(prompt_name) + "\n输出 JSON Schema：\n" + json.dumps(
        schema.model_json_schema(), ensure_ascii=False)
    return schema.model_validate(_call_llm(
        prompt, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), model, trace, stage))


def _contains(text, quote):
    # 只忽略兼容字形和提取空白差异，不允许模型拼接或改写证据。
    normalized = "".join(normalize_text(quote).split())
    return bool(normalized) and normalized in "".join(normalize_text(text).split())


def _validate_evidence(items, resume_text):
    for item in items:
        for quote in item.evidence:
            if not _contains(resume_text, quote):
                raise ValueError("引用证据不在简历原文中")


def resume_analyst_node(state, model, emit_state=None, trace=None):
    if not state.get("resume_text", "").strip():
        raise ValueError("缺少简历文本")
    if emit_state:
        emit_state("提取简历信息中")
    output = _call_llm(_load_prompt("resume_analyst.md"),
                       f"简历文本：\n{normalize_text(state['resume_text'])}", model, trace, "extraction")
    return Extraction.model_validate(output).model_dump()


def context_node(state, model, trace=None):
    previous = GuidanceContext.model_validate(state.get("previous_context") or {})
    hints = resume_target_hints(state["resume_text"])
    resolution = _structured_call("context_resolver.md", {
        "evaluation_date": date.today().isoformat(), "resume_text": normalize_text(state["resume_text"]),
        "extracted_position": state.get("position"), "resume_target_hints": hints,
        "user_message": state.get("user_message", ""), "previous_context": previous.model_dump(),
    }, ContextResolution, model, trace, "context")
    result = GuidanceContext.model_validate(resolution.model_dump(exclude={"clear_fields", "clear_evidence"}))
    if resolution.clear_fields and not _contains(state.get("user_message", ""), resolution.clear_evidence or ""):
        raise ValueError("清空投递信息需要本轮用户原句")
    # 模型不得悄悄丢失已确认项；用户明确否定/撤回时允许清空后追问。
    for key in GuidanceContext.model_fields:
        if key in resolution.clear_fields:
            setattr(result, key, None)
            continue
        signal = getattr(result, key)
        old = getattr(previous, key)
        if signal is None:
            setattr(result, key, old)
            continue
        if old is not None and signal == old:
            continue
        source = state.get("user_message", "") if signal.source == "user" else state["resume_text"]
        if not _contains(source, signal.evidence):
            raise ValueError("投递信息缺少来源原句")
        # 旧用户目标不能被简历意向覆盖；明确标题则可纠正旧的简历推断。
        if old is not None and old.source == "user" and signal.source == "resume":
            setattr(result, key, old)
            continue
        explicit = hints.get(key)
        grounded_heading = (explicit and signal.source == "resume"
                            and _contains(explicit["evidence"], signal.evidence)
                            and (key != "application_type" or signal.value == explicit["value"]))
        if old is not None and old.inferred and not signal.inferred and signal.source != "user" and not grounded_heading:
            raise ValueError("推断的投递信息需要用户确认")
    for key, hint in hints.items():
        signal = getattr(result, key)
        if key not in resolution.clear_fields and (signal is None or (signal.inferred and signal.source == "resume")):
            # 明确标题兜底只补遗漏，不能覆盖用户目标或撤回指令。
            setattr(result, key, ContextSignal.model_validate(hint))
    return {"context": result.model_dump()}


def _review_input(state):
    return {
        "evaluation_date": date.today().isoformat(), "resume_text": normalize_text(state["resume_text"]),
        "context": state["context"], "page_count": state.get("page_count", 1),
        "facts": {key: state.get(key) or [] for key in ("skills", "projects", "experiences", "education")},
    }


def synthesis_node(state, model, trace=None, emit_chunk=None):
    payload = _review_input(state)
    if emit_chunk is None:
        result = _structured_call("report_generator.md", payload, Synthesis, model, trace, "synthesis")
        _validate_evidence([*result.actions, *result.interview_questions], state["resume_text"])
        return {"synthesis": result.model_dump()}

    parser = ReportJSONStream()
    renderer = ReportStreamRenderer(state["context"], emit_chunk,
                                    lambda items: _validate_evidence(items, state["resume_text"]))
    prompt = _load_prompt("report_generator.md") + "\n输出 JSON Schema：\n" + json.dumps(
        Synthesis.model_json_schema(), ensure_ascii=False)
    response = None
    try:
        with closing(model.stream(
            [SystemMessage(content=prompt), HumanMessage(content=json.dumps(payload, ensure_ascii=False))],
            config={"callbacks": [], "run_name": "resume_synthesis", "tags": ["resume_guidance_internal"]},
        )) as chunks:
            for chunk in chunks:
                response = chunk if response is None else response + chunk
                for event in parser.feed(chunk.content):
                    renderer.accept(*event)
        result = Synthesis.model_validate(parser.finish())
        report = Report(context=state["context"], **result.model_dump())
        renderer.finish(report)
    except Exception as error:
        if renderer.parts:
            # 已展示的正文不可撤回，禁止图节点自动重试而重复输出。
            renderer.send(INTERRUPTED)
            raise ReportStreamInterrupted(renderer.content) from error
        raise
    finally:
        if trace and response is not None:
            trace.usage("synthesis", response)
    return {"synthesis": result.model_dump(), "streamed_report": renderer.content}


def report_generator_node(state, model=None, emit_state=None, emit_chunk=None):
    report = Report(context=state["context"], **state["synthesis"])
    if report.context.missing_fields():
        raise ValueError("投递上下文尚未确认")
    content = state.get("streamed_report") or render_report(report)
    if emit_chunk and not state.get("streamed_report"):
        emit_chunk(content)
    return {"report": content, "report_data": report.model_dump()}
