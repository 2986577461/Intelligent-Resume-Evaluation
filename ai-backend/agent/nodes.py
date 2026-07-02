"""Multi-Agent 评分节点 — 从 prompts/ 目录加载提示词"""

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage

from agent.school_tier import classify_school

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_prompt(name: str) -> str:
    path = os.path.join(SCRIPT_DIR, "prompts", name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _call_llm(prompt: str, text: str, model) -> dict:
    """调用 LLM 并解析 JSON 返回"""
    response = model.invoke([
        SystemMessage(content=prompt),
        HumanMessage(content=text),
    ])
    content = response.content.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"raw": content}


# ── 自包含评估节点（提取 + 评分合一，四路并行） ──

def skill_node(state: dict, model, emit_state=None) -> dict:
    """提取技能 + 评分"""
    resume_text = state.get("resume_text", "")
    if emit_state:
        emit_state("提取简历信息中")
    extracted = _call_llm(_load_prompt("extract_basic.md"), f"简历文本：\n{resume_text}", model)
    position = extracted.get("position")
    skills = extracted.get("skills", [])
    result = _call_llm(
        _load_prompt("skill_analyst.md"),
        json.dumps({"skills": skills, "position": position}, ensure_ascii=False, indent=2),
        model,
    )
    return {"skill_result": result}


def education_node(state: dict, model) -> dict:
    """提取学历 + 分类 + 评分"""
    resume_text = state.get("resume_text", "")
    extracted = _call_llm(_load_prompt("extract_education.md"), f"简历文本：\n{resume_text}", model)
    position = extracted.get("position")
    raw_list = extracted.get("education", []) or []
    education = []
    for entry in raw_list:
        if not entry:
            continue
        school = entry.get("school") or ""
        degree = entry.get("degree") or ""
        education.append({
            "school": school,
            "greade": classify_school(school, degree) if school else degree,
            "major": entry.get("major"),
            "时间": entry.get("时间"),
        })
    result = _call_llm(
        _load_prompt("education_analyst.md"),
        json.dumps({"education": education, "position": position}, ensure_ascii=False, indent=2),
        model,
    )
    return {"education_result": result}


def experience_node(state: dict, model) -> dict:
    """提取经历 + 评分"""
    resume_text = state.get("resume_text", "")
    extracted = _call_llm(_load_prompt("extract_experience.md"), f"简历文本：\n{resume_text}", model)
    position = extracted.get("position")
    experiences = extracted.get("experiences", [])
    result = _call_llm(
        _load_prompt("experience_analyst.md"),
        json.dumps({"experiences": experiences, "position": position}, ensure_ascii=False, indent=2),
        model,
    )
    return {"experience_result": result}


def project_node(state: dict, model) -> dict:
    """提取项目 + 评分"""
    resume_text = state.get("resume_text", "")
    extracted = _call_llm(_load_prompt("extract_project.md"), f"简历文本：\n{resume_text}", model)
    position = extracted.get("position")
    projects = extracted.get("projects", [])
    result = _call_llm(
        _load_prompt("project_analyst.md"),
        json.dumps({"projects": projects, "position": position}, ensure_ascii=False, indent=2),
        model,
    )
    return {"project_result": result}


def report_generator_node(state: dict, model, emit_state=None) -> dict:
    """汇总各维度评分生成最终报告"""
    if emit_state:
        emit_state("评估报告生成中")
    results = {
        "skill_match": state.get("skill_result", {}),
        "project_depth": state.get("project_result", {}),
        "experience": state.get("experience_result", {}),
        "education": state.get("education_result", {}),
    }
    text = json.dumps(results, ensure_ascii=False, indent=2)
    result = _call_llm(_load_prompt("report_generator.md"), text, model)

    if isinstance(result, dict) and "raw" not in result:
        return {"report": json.dumps(result, ensure_ascii=False, indent=2)}
    return {"report": result.get("raw", text)}
