"""由已校验生产结果生成展示；无额外模型请求、无未校验 token 流。"""
import html

from agent.schema import Result


def safe(value):
    # 防止简历/JD/模型中的 HTML 或 Markdown 链接成为可执行展示。
    text = html.escape(str(value), quote=True)
    for char in "\\`*_{}[]()#+-.!|>":
        text = text.replace(char, "\\" + char)
    return text


ERROR_HELP = {
    "configuration": "服务端未配置 DEEPSEEK_API_KEY，请配置后重启后端。",
    "authentication": "模型服务鉴权失败（401），请核对 API 密钥及 DEEPSEEK_BASE_URL 是否配套。",
    "quota": "模型服务账户余额或配额不足（402），请核对模型服务账户。",
    "permission": "模型服务拒绝访问（403），请核对账户权限及网关策略。",
    "model_not_found": "模型服务地址或模型不可用（404），请核对 DEEPSEEK_BASE_URL 和 MODEL_NAME。",
    "request_rejected": "模型服务拒绝请求（400/422），请核对网关支持的模型名称及参数。",
    "input_too_large": "模型服务拒绝过大的输入（413），请缩短 JD 或简历后重试。",
    "rate_limit": "模型服务限流，有限重试后仍未完成，请稍后重试。",
    "network": "无法稳定连接模型服务，有限重试后仍未完成，请检查网络和模型服务地址。",
    "timeout": "模型请求超时，有限重试后仍未完成；这不是对简历质量的判断。",
    "budget_exhausted": "本轮处理已超过时间预算，没有把晚到或未完成结果作为成功报告。",
    "invalid_output": "模型输出未通过 JSON、字段或来源校验，未将无效内容作为报告展示。",
    "model_unavailable": "服务端模型初始化失败，请检查依赖和模型配置。",
    "text_unavailable": "此文件没有可用的提取文本，请重新上传。",
    "invalid_cached_extraction": "已保存的事实提取不可用，请重新上传并分析。",
    "model_error": "模型调用发生未分类错误；请按下面的阶段和异常类型排查服务端，不能据此判断简历。",
}


def error_summary(result):
    text = ERROR_HELP.get(result.error_code, "本轮模型处理未完成，请检查服务端配置。")
    detail = result.processing_error
    if detail:
        stages = {"model_init": "模型初始化", "extract": "事实提取", "guide": "岗位证据分析"}
        text += f" 阶段：{stages[detail.stage]}；异常类型：{detail.exception_type}；尝试次数：{detail.attempts}。"
    return text


def omitted_summary(result):
    return f"有 {len(result.omitted_components)} 处可选改写未通过原文或来源约束，已剔除且未展示；核心分析与行动建议已校验。改写部分未完成，不代表整份简历已完成优化。"


def render_markdown(result: Result):
    labels = {"success": "分析完成", "partial": "可用但不完整", "failed": "处理失败", "needs_target": "待确认目标岗位"}
    lines = [f"## 简历修改指导 · {labels[result.status]}", f"目标岗位：{safe(result.target_role or '请确认目标岗位后继续')} ",
             "依据：" + ("已提供 JD；一般补充建议会单独标注。" if result.jd_used else "岗位一般要求，不代表具体企业招聘标准。")]
    if result.status == "needs_target":
        lines.append("这份简历准备投递什么岗位？请在「简历指导」填写并确认目标岗位；JD 可留空。已提取事实会复用。")
    by_id = {s.id: s for s in result.sources}

    def citations(ids):
        for sid in ids:
            source = by_id[sid]
            lines.append(f"> [{safe(sid)}]（{'用户陈述' if source.kind == 'user' else 'JD' if source.kind == 'jd' else '简历原文'}）{safe(source.text)}")

    g = result.guidance
    if g:
        lines.append("### 要求与匹配证据")
        for m in g.matches:
            status = {"evidenced": "有文本证据", "insufficient": "暂无足够信息（不等于不会）", "confirmed_gap": "用户确认尚未做过/掌握"}[m.status]
            lines.extend([f"**{safe(m.id)} · {safe(m.requirement)}**（{'JD 明确要求' if m.basis == 'jd' else '岗位一般建议'}；{status}）",
                          f"观察：{safe(m.observation)}", f"判断依据：{safe(m.rationale)}"])
            citations(m.requirement_source_ids + m.evidence_source_ids)
        lines.append("### 按优先级行动")
        for a in sorted(g.actions, key=lambda a: a.priority):
            lines.extend([f"**P{a.priority} · 对应要求 {safe(a.requirement_id)}**", f"问题：{safe(a.problem)}",
                          f"依据：{safe(a.rationale)}", f"下一步：{safe(a.next_step)}"])
            citations(a.source_ids)
        for q in g.questions:
            lines.extend([f"### 待补充 · {safe(q.question)}", f"为什么问：{safe(q.why)}"])
        for r in g.rewrites:
            lines.extend(["### 局部改写（保守摘编，不覆盖原文件）", "原文：", safe(by_id[r.source_id].text),
                          "建议版本（仅重排/组合有来源的整句；请核对语境后使用）：", safe("\n".join(r.sentences)),
                          f"改动理由：{safe(r.reason)}"])
            citations(r.supporting_source_ids)
            if r.pending_confirmation:
                lines.append("待确认（不属于可直接使用文案）：" + safe("；".join(r.pending_confirmation)))
    elif result.facts:
        lines.append("### 已提取事实（分析未完成时不推断能力缺口）")
        for f in result.facts:
            lines.append(f"- {safe(f.quote)} [{safe(f.source_id)}]")
    lines.append("### 完整性与限制")
    if result.omitted_components:
        lines.append("- " + safe(omitted_summary(result)))
    lines.extend("- " + safe(x) for x in result.limitations + (g.limitations if g else []))
    if result.error_code:
        lines.append("### 处理未完成的原因")
        lines.append(safe(error_summary(result)))
        lines.append("处理状态：" + safe(result.error_code))
    return "\n\n".join(lines)


def render_fallback(result):
    # 独立于富展示的最小确定性模板，仍输出已校验核心建议。
    lines = ["简历指导（基础展示）", "结果状态：" + result.status, "目标岗位：" + safe(result.target_role),
             "引用只表示文本存在；不预测录用结果。"]
    if result.guidance:
        requirements = {m.id: m for m in result.guidance.matches}
        sources = {s.id: s for s in result.sources}
        for a in result.guidance.actions:
            m = requirements[a.requirement_id]
            lines.append(f"P{a.priority} / {safe(m.requirement)}（{'JD 要求' if m.basis == 'jd' else '岗位一般建议'}）：{safe(a.problem)}；依据：{safe(a.rationale)}；下一步：{safe(a.next_step)}")
            for sid in a.source_ids + m.requirement_source_ids:
                lines.append(f"[{safe(sid)}] {safe(sources[sid].text)}")
    lines.extend(safe(x) for x in result.limitations)
    if result.omitted_components:
        lines.append(safe(omitted_summary(result)))
    if result.error_code:
        lines.append(safe(error_summary(result)))
    return "\n\n".join(lines)


def present(result):
    try:
        return render_markdown(result)
    except Exception:
        return render_fallback(result)
