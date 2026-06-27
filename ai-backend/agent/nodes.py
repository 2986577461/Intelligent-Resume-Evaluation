"""Multi-Agent 评分节点"""

import json

from langchain_core.messages import HumanMessage, SystemMessage

ANALYST_PROMPT = """你是一个简历分析师。从以下简历文本中提取结构化信息。

请提取以下字段（只返回JSON，不要其他内容）：
{
  "name": "姓名",
  "skills": ["技能1", "技能2"],
  "education": "最高学历",
  "work_years": 工作年限(数字),
  "projects": ["项目摘要1", "项目摘要2"]
}

如果某个字段无法确定，用 null 代替。"""

SCORER_PROMPT = """你是一个简历评分专家。基于以下简历分析，进行多维度评分。

评分维度及参考权重：
- skill_match (30%)：技能数量、是否掌握主流技术栈、技术广度深度
- project_depth (30%)：项目数量、描述质量、技术复杂度、是否包含量化成果
- experience (20%)：工作/实习年限、成长路径清晰度
- education (20%)：学历层次、专业相关性

每个维度 0-100 分。评分要严格，不要虚高。

返回 JSON 格式（只返回JSON，不要其他内容）：
{
  "overall_score": 总分,
  "dimensions": {
    "skill_match": {"score": 分数, "detail": "评分理由", "evidence": ["证据1"]},
    "project_depth": {"score": 分数, "detail": "评分理由", "evidence": ["证据1"]},
    "experience": {"score": 分数, "detail": "评分理由"},
    "education": {"score": 分数, "detail": "评分理由"}
  },
  "strengths": ["亮点1", "亮点2"],
  "risks": ["风险/改进点1"],
  "interview_questions": ["建议面试问题1"],
  "summary": "一句话总结"
}"""


def resume_analyst_node(state: dict, model) -> dict:
    """提取简历结构化信息"""
    resume_text = state.get("resume_text", "")
    if not resume_text:
        state["analysis"] = {"error": "缺少简历文本"}
        return state

    response = model.invoke([
        SystemMessage(content=ANALYST_PROMPT),
        HumanMessage(content=f"简历文本：\n{resume_text}"),
    ])

    content = response.content.strip()
    # Try to extract JSON from markdown code block if present
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        state["analysis"] = json.loads(content)
    except json.JSONDecodeError:
        state["analysis"] = {"raw": content}

    return state


def scorer_node(state: dict, model) -> dict:
    """基于结构化信息进行多维度评分"""
    analysis = state.get("analysis", {})
    if not analysis:
        state["report"] = "缺少分析数据，无法评分。"
        return state

    response = model.invoke([
        SystemMessage(content=SCORER_PROMPT),
        HumanMessage(content=json.dumps(analysis, ensure_ascii=False, indent=2)),
    ])

    content = response.content.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    state["report"] = content
    return state
