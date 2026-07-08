"""LangGraph 评分工作流 — 并行 Multi-Agent"""

from typing import TypedDict

from langgraph.graph import StateGraph, START
from langgraph.types import RetryPolicy

from agent.nodes import (
    resume_analyst_node,
    skill_analyst_node,
    project_analyst_node,
    experience_analyst_node,
    education_analyst_node,
    layout_analyst_node,
    report_generator_node,
)
from common.logger import get_agent_logger

alog = get_agent_logger()


class EvalState(TypedDict):
    resume_text: str
    analysis: dict
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
    report: str


_LLM_RETRY_POLICY = RetryPolicy(max_attempts=2)


def _wire_scoring_nodes(builder: StateGraph, model, emit_state=None):
    """注册四个维度 analyst + report 汇总节点，供 build_eval_graph / build_scoring_graph 共用

    每个节点单独配置 retry_policy：某个维度因网络抖动/限流等瞬时错误失败时，
    LangGraph 只会重试这一个节点，不影响并行分支里已经跑完的其他维度。
    """
    builder.add_node("skill", lambda s: skill_analyst_node(s, model), retry_policy=_LLM_RETRY_POLICY)
    builder.add_node("project", lambda s: project_analyst_node(s, model), retry_policy=_LLM_RETRY_POLICY)
    builder.add_node("experience", lambda s: experience_analyst_node(s, model), retry_policy=_LLM_RETRY_POLICY)
    builder.add_node("education", lambda s: education_analyst_node(s, model), retry_policy=_LLM_RETRY_POLICY)
    builder.add_node("layout", lambda s: layout_analyst_node(s, model), retry_policy=_LLM_RETRY_POLICY)
    builder.add_node("report", lambda s: report_generator_node(s, model, emit_state), retry_policy=_LLM_RETRY_POLICY)

    builder.add_edge("skill", "report")
    builder.add_edge("project", "report")
    builder.add_edge("experience", "report")
    builder.add_edge("education", "report")
    builder.add_edge("layout", "report")
    builder.set_finish_point("report")


def build_eval_graph(model, emit_state=None):
    """
    构建并行简历评估工作流。

    流程图：
                   ┌─ skill ───┐
                   ├─ project──┤
    resume_analyst ─┼─ exp ─────┤─ report
                   ├─ edu ─────┤
                   └─ layout ──┘

    执行顺序：
    1. resume_analyst → 提取结构化信息（技能、教育、经验、项目等）
    2. skill / project / experience / education / layout → 五个维度独立并行评分
    3. report → 汇总各维度评分，生成最终报告
    """
    builder = StateGraph(EvalState)

    builder.add_node("resume_analyst", lambda s: resume_analyst_node(s, model, emit_state), retry_policy=_LLM_RETRY_POLICY)
    _wire_scoring_nodes(builder, model, emit_state)

    builder.set_entry_point("resume_analyst")
    builder.add_edge("resume_analyst", "skill")
    builder.add_edge("resume_analyst", "project")
    builder.add_edge("resume_analyst", "experience")
    builder.add_edge("resume_analyst", "education")
    builder.add_edge("resume_analyst", "layout")

    return builder.compile()


def build_scoring_graph(model, emit_state=None):
    """
    跳过 resume_analyst，直接从四个维度 analyst 开始并行打分。

    用于 position 缺失中断后、用户补充岗位续跑评分的场景：此时结构化信息
    已经从缓存里拿到，不需要再调用一次 LLM 重新提取简历。
    """
    builder = StateGraph(EvalState)
    _wire_scoring_nodes(builder, model, emit_state)

    builder.add_edge(START, "skill")
    builder.add_edge(START, "project")
    builder.add_edge(START, "experience")
    builder.add_edge(START, "education")
    builder.add_edge(START, "layout")

    return builder.compile()