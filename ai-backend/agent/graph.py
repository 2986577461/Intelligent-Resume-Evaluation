"""LangGraph 评分工作流 — 并行 Multi-Agent"""

from typing import TypedDict

from langgraph.graph import StateGraph

from agent.nodes import (
    resume_analyst_node,
    skill_analyst_node,
    project_analyst_node,
    experience_analyst_node,
    education_analyst_node,
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
    skill_result: dict
    project_result: dict
    experience_result: dict
    education_result: dict
    report: str


def build_eval_graph(model):
    """
    构建并行简历评估工作流。

    流程图：
                   ┌─ skill ─┐
                   ├─ project┤
    resume_analyst ─┼─ exp ───┤─ report
                   └─ edu ───┘

    执行顺序：
    1. resume_analyst → 提取结构化信息（技能、教育、经验、项目等）
    2. skill / project / experience / education → 四个维度独立并行评分
    3. report → 汇总各维度评分，生成最终报告
    """
    builder = StateGraph(EvalState)

    builder.add_node("resume_analyst", lambda s: resume_analyst_node(s, model))
    builder.add_node("skill", lambda s: skill_analyst_node(s, model))
    builder.add_node("project", lambda s: project_analyst_node(s, model))
    builder.add_node("experience", lambda s: experience_analyst_node(s, model))
    builder.add_node("education", lambda s: education_analyst_node(s, model))
    builder.add_node("report", lambda s: report_generator_node(s, model))

    builder.set_entry_point("resume_analyst")
    builder.add_edge("resume_analyst", "skill")
    builder.add_edge("resume_analyst", "project")
    builder.add_edge("resume_analyst", "experience")
    builder.add_edge("resume_analyst", "education")
    builder.add_edge("skill", "report")
    builder.add_edge("project", "report")
    builder.add_edge("experience", "report")
    builder.add_edge("education", "report")
    builder.set_finish_point("report")

    return builder.compile()