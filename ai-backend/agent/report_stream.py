"""总体判断增量解码、建议/追问校验后发布；不将半截 JSON 发给用户。"""

import html
import json

from agent.schema import ActionItem, GuidanceContext, InterviewQuestion, ReportIntroduction

DISCLAIMER = "> 以上是基于简历所提供信息的指导，未核实经历真实性，不代表实际能力或录用结果。\n"
INTERRUPTED = "\n\n> **报告未完成**：后续生成或校验失败，以上仅为已生成部分，请重试获取完整指导。\n"


class ReportStreamInterrupted(Exception):
    def __init__(self, report):
        super().__init__("指导流中断")
        self.report = report


def escape(text):
    return html.escape(str(text), quote=False).replace("|", "\\|").replace("\n", " ")


def introduction_prefix(context):
    lines = [f"**目标岗位：{escape(context.position.value)}｜投递类型：{context.application_type.value}**", ""]
    if context.job_description and not context.job_description.inferred:
        lines.extend(["> 本次结合目标岗位与您提供的招聘要求（JD）分析。", ""])
    else:
        lines.extend(["> 本次按目标岗位分析；如提供具体招聘要求（JD），可进一步对照。", ""])
    lines.extend(["## 总体判断", "", ""])
    return "\n".join(lines)


def introduction(context, intro):
    return introduction_prefix(context) + escape(intro.overview) + "\n\n"


def evidence_text(quotes):
    return "；".join(f"「{escape(quote)}」" for quote in quotes) if quotes else "未提供对应信息，不能据此断言没有相关经历或能力。"


def action_text(item):
    return (f"### {item.id} · {escape(item.subject)}\n\n"
            f"**{item.priority}优先级 · {item.kind}**\n\n"
            f"- 原文依据：{evidence_text(item.evidence)}\n"
            f"- 观察与问题：{escape(item.observation)}\n"
            f"- 岗位影响：{escape(item.impact)}\n"
            f"- 下一步：{escape(item.action)}\n\n")


def question_text(item):
    related = f"（关联 {item.action_id}）" if item.action_id else ""
    return (f"### {escape(item.subject)}{related}\n\n"
            f"- 追问：{escape(item.question)}\n"
            f"- 原文依据：{evidence_text(item.evidence)}\n"
            f"- 考察点：{escape(item.purpose)}\n"
            f"- 准备方向：{escape(item.preparation)}\n\n")


def ordered_actions(items):
    return sorted(items, key=lambda item: {"高": 0, "中": 1, "低": 2}[item.priority])


def actions_text(items):
    body = "".join(action_text(item) for item in ordered_actions(items))
    return "## 优先修改清单\n\n" + (body or "本次未识别出需要优先修改的问题，不为凑数量制造缺点。\n\n")


def render_report(report):
    parts = [introduction(report.context, report), actions_text(report.actions)]
    if report.interview_questions:
        parts.append("## 面试官可能继续追问\n\n")
        parts.extend(question_text(item) for item in report.interview_questions)
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
    """增量解码 overview 文本，其余内容只在真实闭合后发事件，不修补半截 JSON。"""
    arrays = {"actions", "interview_questions"}
    fields = arrays | {"overview"}

    def __init__(self):
        self.buffer, self.pos, self.mode = "", 0, "start"
        self.key, self.index = None, 0
        self.seen = set()
        self.decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
        self.overview_pos = None

    def _overview_delta(self):
        """只消费完整 JSON 字符（含转义和代理对），保留未完成字符等待下一 chunk。"""
        if self.overview_pos is None:
            if self.buffer[self.pos] != '"':
                return ""
            self.overview_pos = self.pos + 1
        pieces = []
        escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
        while self.overview_pos < len(self.buffer):
            start = self.overview_pos
            char = self.buffer[start]
            if char == '"':
                break
            if char == "\\":
                if start + 1 == len(self.buffer):
                    break
                code = self.buffer[start + 1]
                if code in escapes:
                    char, end = escapes[code], start + 2
                elif code == "u":
                    if start + 6 > len(self.buffer):
                        break
                    digits = self.buffer[start + 2:start + 6]
                    if any(c not in "0123456789abcdefABCDEF" for c in digits):
                        raise ValueError("JSON Unicode 转义不合法")
                    point, end = int(digits, 16), start + 6
                    if 0xD800 <= point <= 0xDBFF:
                        if end + 6 > len(self.buffer):
                            break
                        low_escape = self.buffer[end:end + 6]
                        if not low_escape.startswith("\\u") or any(c not in "0123456789abcdefABCDEF" for c in low_escape[2:]):
                            raise ValueError("JSON Unicode 代理对不完整")
                        low = int(low_escape[2:], 16)
                        if not 0xDC00 <= low <= 0xDFFF:
                            raise ValueError("JSON Unicode 代理对不合法")
                        point, end = 0x10000 + ((point - 0xD800) << 10) + low - 0xDC00, end + 6
                    elif 0xDC00 <= point <= 0xDFFF:
                        raise ValueError("JSON Unicode 代理对不合法")
                    char = chr(point)
                else:
                    raise ValueError("JSON 转义不合法")
            else:
                if ord(char) < 0x20 or 0xD800 <= ord(char) <= 0xDFFF:
                    raise ValueError("JSON 字符不合法")
                end = start + 1
            pieces.append(char)
            self.overview_pos = end
        return "".join(pieces)

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
                if self.mode == "value" and self.key == "overview":
                    delta = self._overview_delta()
                    if delta:
                        yield "overview", "delta", delta
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
        self.parts, self.scalars = [], {}
        self.actions, self.questions = [], []
        self.questions_sent = 0
        self.sent_action_ids = set()
        self.overview_raw = self.overview_rendered = ""
        self.intro_started = self.intro_sent = self.actions_sent = self.actions_closed = False

    @property
    def content(self):
        return "".join(self.parts)

    def send(self, text):
        self.parts.append(text)
        self.emit_chunk(text)

    def accept(self, key, index, value):
        if key == "overview" and index == "delta":
            self.overview_raw += value
            # 与 StrictModel 的 strip 一致：末尾空白先保留，遇到后续非空白再发布。
            rendered = escape(self.overview_raw.strip())
            if rendered:
                if not self.intro_started:
                    self.send(introduction_prefix(self.context))
                    self.intro_started = True
                if not rendered.startswith(self.overview_rendered):
                    raise ValueError("总体判断增量不一致")
                delta = rendered[len(self.overview_rendered):]
                if delta:
                    self.send(delta)
                self.overview_rendered = rendered
            return
        if index == "end":
            if key == "actions":
                self.actions_closed = True
        elif index is None:
            self.scalars[key] = value
        else:
            schema = {"actions": ActionItem, "interview_questions": InterviewQuestion}[key]
            item = schema.model_validate(value)
            self.validate_evidence([item])
            items = self.actions if key == "actions" else self.questions
            if len(items) >= (3 if key == "actions" else 5):
                raise ValueError("报告条目过多")
            if key == "actions" and any(old.id == item.id for old in items):
                raise ValueError("建议 ID 不得重复")
            items.append(item)
        self.flush()

    def flush(self):
        if not self.intro_sent:
            if set(self.scalars) != {"overview"}:
                return
            intro = ReportIntroduction.model_validate(self.scalars)
            if self.intro_started:
                if escape(intro.overview) != self.overview_rendered:
                    raise ValueError("总体判断增量与最终值不一致")
                self.send("\n\n")
            else:
                self.send(introduction(self.context, intro))
            self.intro_sent = True
        if (self.actions or self.actions_closed) and not self.actions_sent:
            self.send("## 优先修改清单\n\n")
            self.actions_sent = True
        # 高优先级项无需等整个数组；中低优先级暂存到闭合，保持稳定排序且不回改正文。
        for item in ordered_actions(self.actions):
            if item.id not in self.sent_action_ids and (self.actions_closed or item.priority == "高"):
                self.send(action_text(item))
                self.sent_action_ids.add(item.id)
        if not self.actions_closed:
            return
        if not self.actions and "empty" not in self.sent_action_ids:
            self.send("本次未识别出需要优先修改的问题，不为凑数量制造缺点。\n\n")
            # 空清单只发布一次；后续追问事件还会调用 flush。
            self.sent_action_ids.add("empty")
        while self.questions_sent < len(self.questions):
            item = self.questions[self.questions_sent]
            if item.action_id is not None and item.action_id not in {action.id for action in self.actions}:
                raise ValueError("追问关联的建议不存在")
            if self.questions_sent == 0:
                self.send("## 面试官可能继续追问\n\n")
            self.send(question_text(item))
            self.questions_sent += 1

    def finish(self, report):
        # 同一渲染协议确保 SSE、工具返回和持久化完全相同。
        expected = render_report(report)
        tail = "\n" + DISCLAIMER
        if self.content + tail != expected:
            raise ValueError("报告流与最终协议不一致")
        self.send(tail)
