"""Multi-Agent 评分节点：事实提取、证据依赖、规则算分与确定性展示。"""

import json
import os
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage

from agent.schema import (
    AnalystResult, DIMENSION_LABELS, DIMENSION_WEIGHTS, Extraction, Report, Synthesis, validate_dimension,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_prompt(name: str) -> str:
    with open(os.path.join(SCRIPT_DIR, "prompts", name), encoding="utf-8") as file:
        return file.read().strip()


def _call_llm(prompt: str, text: str, model, trace=None, stage="internal") -> dict:
    """内部 JSON 调用不向聊天泄漏 token；非法输出抛错，由所在节点有限重试。"""
    response = model.invoke(
        [SystemMessage(content=prompt), HumanMessage(content=text)],
        config={"callbacks": [], "run_name": f"resume_{stage}", "tags": ["multi_agent_internal"]},
    )
    if trace:
        trace.usage(stage, response)
    content = response.content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("模型必须返回 JSON 对象")
    return result


def resume_analyst_node(state: dict, model, emit_state=None, trace=None) -> dict:
    if not state.get("resume_text", "").strip():
        raise ValueError("缺少简历文本")
    if emit_state:
        emit_state("提取简历信息中")
    output = _call_llm(_load_prompt("resume_analyst.md"),
                       f"简历文本：\n{state['resume_text']}", model, trace, "extraction")
    return Extraction.model_validate(output).model_dump()


def _analyst_input(state: dict, key: str) -> str:
    data = {key: state.get(key) or [], "position": state.get("position")}
    if key == "skills":
        # 技能判断必须等待项目分析，并同时拿到项目/经历原始事实，不能只信下游结论。
        data.update(projects=state.get("projects") or [],
                    experiences=state.get("experiences") or [],
                    project_result=state["project_result"],
                    resume_text=state.get("resume_text", ""))
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _analyst(state, model, key, dimension, trace=None):
    prompt = (_load_prompt(f"{key}_analyst.md")
              + "\n只输出1–3条精炼理由，每条约80–120字，保留关键证据、判断与重要信息不足，不逐条复述整个输入。"
              + "\n输出 JSON Schema：\n" + json.dumps(AnalystResult.model_json_schema(), ensure_ascii=False))
    result = AnalystResult.model_validate(_call_llm(
        prompt, _analyst_input(state, {"skill": "skills", "project": "projects",
                                      "experience": "experiences", "education": "education"}[key]),
        model, trace, key))
    # 模型只判断原始分，权重换算交给代码，消除原始分/加权分混淆。
    weighted = {"score": round(result.raw_score * DIMENSION_WEIGHTS[dimension] / 100, 2),
                "detail": result.detail}
    return {f"{key}_result": validate_dimension(weighted, dimension)}


def skill_analyst_node(state, model, trace=None):
    return _analyst(state, model, "skill", "skill_match", trace)


def project_analyst_node(state, model, trace=None):
    return _analyst(state, model, "project", "project_depth", trace)


def experience_analyst_node(state, model, trace=None):
    return _analyst(state, model, "experience", "experience", trace)


def education_analyst_node(state, model, trace=None):
    return _analyst(state, model, "education", "education", trace)


def layout_analyst_node(state, model=None):
    """复现既有页数规则，不再请求模型；并非 PDF 视觉布局分析。"""
    pages = state.get("page_count", 1)
    if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
        raise ValueError("无效页数")
    result = {"score": 5 if pages == 1 else 0,
              "detail": ["简历为1页，篇幅合理" if pages == 1 else
                         f"简历共{pages}页，页数超过1页，建议缩减到1页"]}
    return {"layout_result": result}


def _dimensions(state):
    return {name: validate_dimension(state[f"{key}_result"], name) for name, key in (
        ("skill_match", "skill"), ("project_depth", "project"), ("experience", "experience"),
        ("education", "education"), ("layout", "layout"),
    )}


def _escape(text):
    # 模型内容只作为普通段落/表格文本展示，不允许注入 HTML 或破坏表格。
    import html
    return html.escape(str(text), quote=False).replace("|", "\\|").replace("\n", " ")


def scorecard_node(state):
    """校验维度并准备评分表，仅写入内部状态，不提前输出半份分析。"""
    dimensions = _dimensions(state)
    total = round(sum(value["score"] for value in dimensions.values()), 2)
    lines = [f"**目标岗位：{_escape(state['position'])}**",
             f"**总分：{total:g} / 100**", "", "| 维度 | 得分 | 评价 |", "| --- | ---: | --- |"]
    for name, value in dimensions.items():
        lines.append(f"| {DIMENSION_LABELS[name]} | {value['score']:g} / {DIMENSION_WEIGHTS[name]} | "
                     + "；".join(_escape(item) for item in value["detail"]) + " |")
    lines.extend(["", ""])
    return {"scorecard": "\n".join(lines)}


def synthesis_node(state, model, trace=None):
    """保留完整跨维度综合分析，只不让模型重复算分、重写评分表。"""
    text = json.dumps({
        "evaluation_date": date.today().isoformat(),
        "resume_text": state.get("resume_text", ""),
        "position": state["position"], "dimensions": _dimensions(state),
        "facts": {key: state.get(key) or [] for key in ("skills", "projects", "experiences", "education")},
    }, ensure_ascii=False, separators=(",", ":"))
    prompt = _load_prompt("report_generator.md") + "\n输出 JSON Schema：\n" + json.dumps(
        Synthesis.model_json_schema(), ensure_ascii=False)
    result = Synthesis.model_validate(_call_llm(prompt, text, model, trace, "synthesis"))
    # 页数风险是确定规则，不能依赖模型是否记得重述。
    if state["layout_result"]["score"] < DIMENSION_WEIGHTS["layout"]:
        layout_risk = state["layout_result"]["detail"][0]
        if not any("页" in risk for risk in result.risks):
            result.risks = result.risks[:4] + [layout_risk]
    return {"synthesis": result.model_dump()}


def report_generator_node(state, model=None, emit_state=None, emit_chunk=None):
    dimensions = _dimensions(state)
    report = Report(overall_score=round(sum(item["score"] for item in dimensions.values()), 2),
                    dimensions=dimensions, **state["synthesis"])
    lines = []
    for title, items in (("优势", report.strengths), ("风险与改进建议", report.risks),
                         ("针对性面试追问", report.interview_questions)):
        lines.extend([f"## {title}", ""] + [f"- {_escape(item)}" for item in items] + [""])
    lines.extend(["## 总结", "", _escape(report.summary), "",
                  "> 以上是基于简历文本和现有评分规则的评估，不代表实际能力或录用概率。", ""])
    content = state["scorecard"] + "\n".join(lines)
    if emit_chunk:
        emit_chunk(content)
    return {"report": content, "report_data": report.model_dump()}
