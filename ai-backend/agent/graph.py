"""LangGraph 评分工作流"""

from typing import TypedDict

from langgraph.graph import StateGraph

from agent.nodes import resume_analyst_node, scorer_node


class EvalState(TypedDict):
    resume_text: str
    question: str
    analysis: dict
    report: str


def build_eval_graph(model):
    """构建简历评估多 Agent 工作流"""
    builder = StateGraph(EvalState)

    builder.add_node("resume_analyst", lambda s: resume_analyst_node(s, model))
    builder.add_node("scorer", lambda s: scorer_node(s, model))

    builder.add_edge("resume_analyst", "scorer")
    builder.set_entry_point("resume_analyst")
    builder.set_finish_point("scorer")

    return builder.compile()
