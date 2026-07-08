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
    """调用 LLM 并解析 JSON 返回。

    这个调用发生在外层对话 agent 的工具执行过程中。LangChain 的回调管理器默认按
    上下文（contextvars）传播，如果不显式清空 callbacks，这次内部调用的输出会被
    外层 `agent.stream(..., stream_mode="messages")` 当成对话回复的一部分转发给用户，
    造成"内部提取结果泄漏成聊天内容"的问题。传空 callbacks + 独立 run_name/tags 隔离掉。
    """
    response = model.invoke(
        [SystemMessage(content=prompt), HumanMessage(content=text)],
        config={"callbacks": [], "run_name": "multi_agent_internal_call", "tags": ["multi_agent_internal"]},
    )
    content = response.content.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    return json.loads(content)



def resume_analyst_node(state: dict, model, emit_state=None) -> dict:
    """LLM 提取简历结构化信息"""
    resume_text = state.get("resume_text", "")
    if not resume_text:
        return {"error": "缺少简历文本"}
    if emit_state:
        emit_state("提取简历信息中")
    prompt = _load_prompt("resume_analyst.md")
    output = _call_llm(prompt, f"简历文本：\n{resume_text}", model)
    print(output)
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