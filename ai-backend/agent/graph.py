"""LangGraph 评分工作流 — 并行 Multi-Agent"""

from typing import TypedDict

from langgraph.graph import START, END, StateGraph

from agent.nodes import (
    skill_node,
    education_node,
    experience_node,
    project_node,
    report_generator_node,
)
from common.logger import get_agent_logger

alog = get_agent_logger()


class EvalState(TypedDict):
    resume_text: str
    skill_result: dict
    project_result: dict
    experience_result: dict
    education_result: dict
    report: str


def build_eval_graph(model, emit_state=None):
    """
    并行简历评估工作流。

    START → skill / education / experience / project (四路并行，各自提取+评分) → report → END
    """
    builder = StateGraph(EvalState)

    builder.add_node("skill", lambda s: skill_node(s, model, emit_state))
    builder.add_node("education", lambda s: education_node(s, model))
    builder.add_node("experience", lambda s: experience_node(s, model))
    builder.add_node("project", lambda s: project_node(s, model))
    builder.add_node("report", lambda s: report_generator_node(s, model, emit_state))

    builder.add_edge(START, "skill")
    builder.add_edge(START, "education")
    builder.add_edge(START, "experience")
    builder.add_edge(START, "project")

    builder.add_edge("skill", "report")
    builder.add_edge("education", "report")
    builder.add_edge("experience", "report")
    builder.add_edge("project", "report")

    builder.add_edge("report", END)

    return builder.compile()
