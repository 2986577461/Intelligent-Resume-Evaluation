"""对话式简历指导：提取、目标确认、单次综合分析与确定性渲染。"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from agent.nodes import context_node, report_generator_node, resume_analyst_node, synthesis_node
from agent.schema import GuidanceContext


class EvalState(TypedDict, total=False):
    resume_text: str
    user_message: str
    previous_context: dict
    context: dict
    position: str | None
    skills: list
    education: list
    experiences: list
    projects: list
    page_count: int
    synthesis: dict
    streamed_report: str
    report: str
    report_data: dict


def _retryable(error):
    if isinstance(error, (ValueError, TimeoutError, ConnectionError)):
        return True
    status = getattr(error, "status_code", None)
    return status in (408, 409, 429) or (isinstance(status, int) and status >= 500) or type(error).__name__ in {
        "APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout",
    }


_LLM_RETRY_POLICY = RetryPolicy(max_attempts=2, retry_on=_retryable)


def _build(model, emit_state, emit_chunk, trace, include_extraction, include_context):
    builder = StateGraph(EvalState)

    def wrap(name, fn):
        def run(state):
            return trace.run(name, lambda: fn(state)) if trace else fn(state)
        return run

    def synthesize(state):
        if emit_state:
            emit_state("优化建议与面试追问生成中")
        return synthesis_node(state, model, trace, emit_chunk=emit_chunk)

    builder.add_node("synthesis", wrap("synthesis", synthesize), retry_policy=_LLM_RETRY_POLICY)
    builder.add_node("report", wrap("report", lambda s: report_generator_node(s, emit_chunk=emit_chunk)))
    entry = START
    if include_extraction:
        builder.add_node("extraction", wrap("extraction", lambda s: resume_analyst_node(s, model, emit_state, trace)),
                         retry_policy=_LLM_RETRY_POLICY)
        builder.add_edge(entry, "extraction")
        entry = "extraction"
    if include_context:
        builder.add_node("context", wrap("context", lambda s: context_node(s, model, trace)),
                         retry_policy=_LLM_RETRY_POLICY)
        builder.add_edge(entry, "context")
        builder.add_conditional_edges("context", lambda s: "wait" if GuidanceContext.model_validate(
            s["context"]).missing_fields() else "analyze", {"wait": END, "analyze": "synthesis"})
    else:
        builder.add_edge(entry, "synthesis")
    builder.add_edge("synthesis", "report")
    builder.add_edge("report", END)
    return builder.compile()


def build_eval_graph(model, emit_state=None, emit_chunk=None, trace=None):
    return _build(model, emit_state, emit_chunk, trace, include_extraction=True, include_context=True)


def build_scoring_graph(model, emit_state=None, emit_chunk=None, trace=None):
    """保留既有 Python 入口名。输入须有完整 context，内部只调用一次综合分析。"""
    return _build(model, emit_state, emit_chunk, trace, include_extraction=False, include_context=False)


def build_extraction_graph(model, emit_state=None, trace=None):
    builder = StateGraph(EvalState)

    def extract(state):
        fn = lambda: resume_analyst_node(state, model, emit_state, trace)
        return trace.run("extraction", fn) if trace else fn()

    builder.add_node("extraction", extract, retry_policy=_LLM_RETRY_POLICY)
    builder.add_edge(START, "extraction")
    builder.add_edge("extraction", END)
    return builder.compile()


def build_context_graph(model, trace=None):
    builder = StateGraph(EvalState)

    def resolve(state):
        fn = lambda: context_node(state, model, trace)
        return trace.run("context", fn) if trace else fn()

    builder.add_node("context", resolve, retry_policy=_LLM_RETRY_POLICY)
    builder.add_edge(START, "context")
    builder.add_edge("context", END)
    return builder.compile()
