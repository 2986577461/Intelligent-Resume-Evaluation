"""LangGraph 混合评分：项目 → 技能的真实依赖，其余维度并行。"""

from typing import TypedDict

from langgraph.graph import StateGraph, START
from langgraph.types import RetryPolicy

from agent.nodes import (
    resume_analyst_node, skill_analyst_node, project_analyst_node, experience_analyst_node,
    education_analyst_node, layout_analyst_node, scorecard_node, synthesis_node, report_generator_node,
)


class EvalState(TypedDict):
    resume_text: str
    position: str
    skills: list
    education: list
    experiences: list
    projects: list
    page_count: int
    skill_result: dict
    project_result: dict
    experience_result: dict
    education_result: dict
    layout_result: dict
    scorecard: str
    synthesis: dict
    report: str
    report_data: dict


def _retryable(error):
    # 格式错误重试；鉴权/权限/参数错误不重复付费。SDK 自己不重试。
    if isinstance(error, (ValueError, TimeoutError, ConnectionError)):
        return True
    status = getattr(error, "status_code", None)
    return status in (408, 409, 429) or (isinstance(status, int) and status >= 500) or type(error).__name__ in {
        "APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout",
    }


_LLM_RETRY_POLICY = RetryPolicy(max_attempts=2, retry_on=_retryable)


def _build(model, emit_state, emit_chunk, trace, include_extraction):
    builder = StateGraph(EvalState)

    def wrap(name, fn, completed=None):
        def run(state):
            result = trace.run(name, lambda: fn(state)) if trace else fn(state)
            if completed and emit_state:
                emit_state(completed)
            return result
        return run

    for name, fn, message in (
        ("project", lambda s: project_analyst_node(s, model, trace), "项目分析完成，开始核验技能佐证"),
        ("skill", lambda s: skill_analyst_node(s, model, trace), "技能分析完成"),
        ("experience", lambda s: experience_analyst_node(s, model, trace), "经历分析完成"),
        ("education", lambda s: education_analyst_node(s, model, trace), "学历分析完成"),
    ):
        builder.add_node(name, wrap(name, fn, message), retry_policy=_LLM_RETRY_POLICY)
    builder.add_node("layout", wrap("layout", layout_analyst_node, "版式规则计算完成"))
    builder.add_node("scorecard", wrap("scorecard", scorecard_node))

    def synthesize(state):
        if emit_state:
            emit_state("评分已完成，综合分析生成中")
        return synthesis_node(state, model, trace)

    builder.add_node("synthesis", wrap("synthesis", synthesize), retry_policy=_LLM_RETRY_POLICY)
    builder.add_node("report", wrap("report", lambda s: report_generator_node(s, emit_chunk=emit_chunk)))

    entry = START
    if include_extraction:
        builder.add_node("resume_analyst", wrap("extraction", lambda s: resume_analyst_node(s, model, emit_state, trace)),
                         retry_policy=_LLM_RETRY_POLICY)
        builder.add_edge(START, "resume_analyst")
        entry = "resume_analyst"
    for branch in ("project", "experience", "education", "layout"):
        builder.add_edge(entry, branch)
    builder.add_edge("project", "skill")
    # 显式多源 barrier：所有维度完成后内部算分，再综合分析，最后一次性输出完整内容。
    builder.add_edge(["skill", "experience", "education", "layout"], "scorecard")
    builder.add_edge("scorecard", "synthesis")
    builder.add_edge("synthesis", "report")
    builder.set_finish_point("report")
    return builder.compile()


def build_eval_graph(model, emit_state=None, emit_chunk=None, trace=None):
    return _build(model, emit_state, emit_chunk, trace, include_extraction=True)


def build_scoring_graph(model, emit_state=None, emit_chunk=None, trace=None):
    return _build(model, emit_state, emit_chunk, trace, include_extraction=False)


def build_extraction_graph(model, emit_state=None, trace=None):
    """工具路径也使用节点级重试，成功结果才进入缓存。"""
    builder = StateGraph(EvalState)

    def extract(state):
        fn = lambda: resume_analyst_node(state, model, emit_state, trace)
        return trace.run("extraction", fn) if trace else fn()

    builder.add_node("extraction", extract, retry_policy=_LLM_RETRY_POLICY)
    builder.add_edge(START, "extraction")
    builder.set_finish_point("extraction")
    return builder.compile()
