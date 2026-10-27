"""PDF 字形归一化，以及仅针对明确标题/求职意向的保守兜底。"""

import re
import unicodedata


def normalize_text(text):
    # PDF 常将“用、生、目”等编码为康熙部首；不是 OCR 丢字。
    return unicodedata.normalize("NFKC", text)


def resume_target_hints(text):
    lines = [line.strip() for line in normalize_text(text).splitlines() if line.strip()]
    candidates = []
    for index, line in enumerate(lines[:12]):
        explicit = re.fullmatch(r"(?:求职意向|目标岗位|应聘岗位|求职岗位)\s*[:：]\s*(.{2,60})", line)
        if explicit:
            candidates.append(explicit.group(1))
        elif index == 0 and re.fullmatch(
            r"[\w\s/+.#\-·（）()]{2,50}(?:实习生|工程师|开发|设计师|产品经理|运营|分析师)(?:[（(](?:实习|校招|社招)[）)])?", line):
            candidates.append(line)
    # 多个不同目标不自动选；仅从标题明示文本取值，不能凭技能补全。
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        return {}
    title = candidates[0]
    hints = {"position": {"value": title, "source": "resume", "evidence": title, "inferred": False}}
    application_types = [kind for kind, pattern in (
        ("实习", r"实习"), ("校招", r"校招|校园招聘"), ("社招", r"社招|社会招聘"),
    ) if re.search(pattern, title)]
    if len(application_types) == 1:
        hints["application_type"] = {"value": application_types[0], "source": "resume", "evidence": title, "inferred": False}
    return hints
