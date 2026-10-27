"""将模型的 JSON 流按完整、已校验的语义段落渲染；不将半截 JSON 发给用户。"""

import html
import json

from agent.schema import (ActionItem, GuidanceContext, InterviewQuestion, MODULE_LABELS,
                          ReportIntroduction, ReviewSection)

DISCLAIMER = "> 以上是基于简历所提供信息的指导，未核实经历真实性，不代表实际能力或录用结果。\n"
INTERRUPTED = "\n\n> **报告未完成**：后续生成或校验失败，以上仅为已生成部分，请重试获取完整指导。\n"


class ReportStreamInterrupted(Exception):
    def __init__(self, report):
        super().__init__("指导流中断")
        self.report = report


def escape(text):
    return html.escape(str(text), quote=False).replace("|", "\\|").replace("\n", " ")


def introduction(context, intro):
    lines = [f"**目标岗位：{escape(context.position.value)}｜投递类型：{context.application_type.value}**", ""]
    if context.job_description and not context.job_description.inferred:
        lines.extend(["> 本次结合目标岗位与您提供的招聘要求（JD）分析。", ""])
    else:
        lines.extend(["> 本次按目标岗位分析；如提供具体招聘要求（JD），可进一步对照。", ""])
    lines.extend(["## 总体判断", "", f"**{intro.match_status}**。{escape(intro.overview)}", "", ""])
    return "\n".join(lines)


def section_text(section):
    return f"## {MODULE_LABELS[section.module]}\n\n{escape(section.commentary)}\n\n"


def action_text(item):
    return f"- **{item.priority}优先级 · {item.kind}**：{escape(item.action)}\n"


def ordered_actions(items):
    return sorted(items, key=lambda item: {"高": 0, "中": 1, "低": 2}[item.priority])


def render_report(report):
    parts = [introduction(report.context, report)]
    sections = {section.module: section for section in report.sections}
    parts.extend(section_text(sections[module]) for module in MODULE_LABELS)
    parts.append("## 优先修改清单\n\n")
    parts.extend(action_text(item) for item in ordered_actions(report.actions))
    if report.interview_questions:
        parts.append("\n## 面试官可能继续追问\n\n")
        parts.extend(f"- {escape(item.question)}\n" for item in report.interview_questions)
    parts.append("\n" + DISCLAIMER)
    return "".join(parts)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 字段重复")
        result[key] = value
    return result


class ReportJSONStream:
    """小型增量解析器：只在值真实闭合后发事件，不补造结束引号/括号。"""
    arrays = {"sections", "actions", "interview_questions"}
    fields = arrays | {"match_status", "overview"}

    def __init__(self):
        self.buffer, self.pos, self.mode = "", 0, "start"
        self.key, self.index = None, 0
        self.seen = set()
        self.decoder = json.JSONDecoder(object_pairs_hook=_unique_object)

    def feed(self, text):
        self.buffer += text
        if len(self.buffer) > 250_000:
            raise ValueError("报告过长")
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos].isspace():
                self.pos += 1
            if self.pos == len(self.buffer):
                return
            char = self.buffer[self.pos]
            if self.mode == "start":
                if char != "{":
                    raise ValueError("报告必须为 JSON 对象")
                self.pos += 1
                self.mode = "first_key"
            elif self.mode in {"first_key", "key"}:
                if char == "}" and self.mode == "first_key":
                    self.pos += 1
                    self.mode = "done"
                    continue
                try:
                    key, end = self.decoder.raw_decode(self.buffer, self.pos)
                except json.JSONDecodeError:
                    return
                if not isinstance(key, str) or key not in self.fields or key in self.seen:
                    raise ValueError("未知或重复的报告字段")
                self.key, self.pos, self.mode = key, end, "colon"
                self.seen.add(key)
            elif self.mode == "colon":
                if char != ":":
                    raise ValueError("JSON 字段缺少冒号")
                self.pos += 1
                self.mode = "value"
            elif self.mode == "value" and self.key in self.arrays:
                if char != "[":
                    raise ValueError("报告集合必须为数组")
                self.pos += 1
                self.index, self.mode = 0, "first_item"
            elif self.mode in {"value", "first_item", "item"}:
                if char == "]" and self.mode == "first_item":
                    self.pos += 1
                    self.mode = "field_sep"
                    yield self.key, "end", None
                    continue
                try:
                    value, end = self.decoder.raw_decode(self.buffer, self.pos)
                except json.JSONDecodeError:
                    return
                self.pos = end
                if self.mode == "value":
                    self.mode = "field_sep"
                    yield self.key, None, value
                else:
                    index = self.index
                    self.index += 1
                    self.mode = "item_sep"
                    yield self.key, index, value
            elif self.mode == "item_sep":
                if char not in ",]":
                    raise ValueError("JSON 数组分隔错误")
                self.pos += 1
                self.mode = "item" if char == "," else "field_sep"
                if char == "]":
                    yield self.key, "end", None
            elif self.mode == "field_sep":
                if char not in ",}":
                    raise ValueError("JSON 对象分隔错误")
                self.pos += 1
                self.mode = "key" if char == "," else "done"
            else:
                raise ValueError("报告 JSON 后含有额外内容")

    def finish(self):
        if self.mode != "done":
            raise ValueError("报告 JSON 未完成")
        return json.loads(self.buffer, object_pairs_hook=_unique_object)


class ReportStreamRenderer:
    def __init__(self, context, emit_chunk, validate_evidence):
        self.context = GuidanceContext.model_validate(context)
        if self.context.missing_fields():
            raise ValueError("投递上下文尚未确认")
        self.emit_chunk, self.validate_evidence = emit_chunk, validate_evidence
        self.parts, self.scalars, self.sections = [], {}, {}
        self.actions, self.questions = [], []
        self.sections_sent = self.questions_sent = 0
        self.intro_sent = self.actions_sent = self.actions_closed = False

    @property
    def content(self):
        return "".join(self.parts)

    def send(self, text):
        self.parts.append(text)
        self.emit_chunk(text)

    def accept(self, key, index, value):
        if index == "end":
            if key == "sections" and len(self.sections) != 5:
                raise ValueError("指导模块不完整")
            if key == "actions":
                if not 1 <= len(self.actions) <= 5:
                    raise ValueError("行动条数不合法")
                self.actions_closed = True
        elif index is None:
            self.scalars[key] = value
        else:
            schema = {"sections": ReviewSection, "actions": ActionItem, "interview_questions": InterviewQuestion}[key]
            item = schema.model_validate(value)
            self.validate_evidence([item])
            if key == "sections":
                if item.module in self.sections:
                    raise ValueError("指导模块重复")
                self.sections[item.module] = item
            else:
                items = self.actions if key == "actions" else self.questions
                if len(items) >= (5 if key == "actions" else 6):
                    raise ValueError("报告条目过多")
                items.append(item)
        self.flush()

    def flush(self):
        if not self.intro_sent:
            if set(self.scalars) != {"match_status", "overview"}:
                return
            intro = ReportIntroduction.model_validate(self.scalars)
            self.send(introduction(self.context, intro))
            self.intro_sent = True
        modules = list(MODULE_LABELS)
        while self.sections_sent < len(modules) and modules[self.sections_sent] in self.sections:
            self.send(section_text(self.sections[modules[self.sections_sent]]))
            self.sections_sent += 1
        if self.sections_sent != len(modules) or not self.actions_closed:
            return
        if not self.actions_sent:
            self.send("## 优先修改清单\n\n")
            for action in ordered_actions(self.actions):
                self.send(action_text(action))
            self.actions_sent = True
        while self.questions_sent < len(self.questions):
            if self.questions_sent == 0:
                self.send("\n## 面试官可能继续追问\n\n")
            self.send(f"- {escape(self.questions[self.questions_sent].question)}\n")
            self.questions_sent += 1

    def finish(self, report):
        # 同一渲染协议确保 SSE、工具返回和持久化完全相同。
        expected = render_report(report)
        tail = "\n" + DISCLAIMER
        if self.content + tail != expected:
            raise ValueError("报告流与最终协议不一致")
        self.send(tail)
