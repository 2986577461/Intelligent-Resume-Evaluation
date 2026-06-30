"""Multi-Agent 评分节点 — 从 prompts/ 目录加载提示词"""

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage

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


def resume_analyst_node(state: dict, model, emit_state=None) -> dict:
    """LLM 提取简历结构化信息"""
    resume_text = state.get("resume_text", "")
    if not resume_text:
        return {"error": "缺少简历文本"}
    if emit_state:
        emit_state("提取简历信息中")
    prompt = _load_prompt("resume_analyst.md")
    output = _call_llm(prompt, f"简历文本：\n{resume_text}", model)
    return output


def _analyst_input(state: dict, key: str) -> str:
    """构建 analyst 的输入：{数据, position}"""
    return json.dumps({
        key: state.get(key, []),
        "position": state.get("position"),
    }, ensure_ascii=False, indent=2)


def skill_analyst_node(state: dict, model) -> dict:
    """技能匹配度评估"""
    prompt = _load_prompt("skill_analyst.md")
    return {"skill_result": _call_llm(prompt, _analyst_input(state, "skills"), model)}


def project_analyst_node(state: dict, model) -> dict:
    """项目深度评估"""
    prompt = _load_prompt("project_analyst.md")
    return {"project_result": _call_llm(prompt, _analyst_input(state, "projects"), model)}


def experience_analyst_node(state: dict, model) -> dict:
    """经验背景评估"""
    prompt = _load_prompt("experience_analyst.md")
    return {"experience_result": _call_llm(prompt, _analyst_input(state, "experiences"), model)}


def education_analyst_node(state: dict, model) -> dict:
    """学历背景评估"""
    prompt = _load_prompt("education_analyst.md")
    return {"education_result": _call_llm(prompt, _analyst_input(state, "education"), model)}


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
    prompt = _load_prompt("report_generator.md")
    result = _call_llm(prompt, text, model)

    if isinstance(result, dict) and "raw" not in result:
        return {"report": json.dumps(result, ensure_ascii=False, indent=2)}
    return {"report": result.get("raw", text)}