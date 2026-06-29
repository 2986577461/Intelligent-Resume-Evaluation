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


class EvalState(TypedDict):
    resume_text: str
    analysis: dict
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
    1. resume_analyst → 提取结构化信息（姓名、技能、教育、年限、项目）
    2. skill / project / experience / education → 四个维度独立并行评分
    3. report → 汇总各维度评分，按权重计算总分，生成最终报告

    LangGraph fan-out 机制：resume_analyst 完成后，四个 analyst
    在同一个 superstep 中并行执行，互不干扰。
    """
    builder = StateGraph(EvalState)

    builder.add_node("resume_analyst", lambda s: resume_analyst_node(s, model))
    builder.add_node("skill", lambda s: skill_analyst_node(s, model))
    builder.add_node("project", lambda s: project_analyst_node(s, model))
    builder.add_node("experience", lambda s: experience_analyst_node(s, model))
    builder.add_node("education", lambda s: education_analyst_node(s, model))
    builder.add_node("report", lambda s: report_generator_node(s, model))

    builder.set_entry_point("resume_analyst")
    # fan-out：一个上游分叉到四个并行节点
    builder.add_edge("resume_analyst", "skill")
    builder.add_edge("resume_analyst", "project")
    builder.add_edge("resume_analyst", "experience")
    builder.add_edge("resume_analyst", "education")
    # fan-in：四个节点汇合到 report
    builder.add_edge("skill", "report")
    builder.add_edge("project", "report")
    builder.add_edge("experience", "report")
    builder.add_edge("education", "report")
    builder.set_finish_point("report")

    return builder.compile()

# ```json
# {
#   "overall_score": ,
#   "dimensions": {
#     "skill_match": {
#       "score": ,
#       "detail": ""
#     },
#     "project_depth": {
#       "score": 25,
#       "detail": ""
#     },
#     "experience": {
#       "score": 20,
#       "detail": ""
#     },
#     "education": {
#       "score": 45,
#       "detail": ""
#     }
#   },
#   "strengths": [
#     "",
#     ""
#   ],
#   "risks": [
#     "",
#     "",
#     ""
#   ],
#   "interview_questions": [
#     "",
#     "如果评论表数据量达到千万级，你会如何设计分页和缓存方案来保证响应速度？",
#     "描述一个你在项目开发中遇到的最棘手的技术问题，你是如何定位并解决的？"
#   ],
#   "summary": "该候选人技术广度尚可，但项目经验浅、学历偏低，整体竞争力较弱，需通过实习或深度项目提升实践能力。"
# }
# ```