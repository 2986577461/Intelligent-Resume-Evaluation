"""SSE 信号常量与工具函数"""

import json


class SSE:
    DONE = "[DONE]"
    ERROR = "[ERROR]"

    # Tool → SSE state 注册表
    # calling: 工具被调用时发送的状态
    # done:    工具返回结果时发送的状态
    TOOLS = {
        "tavily":         {"calling": "Searching the web", "done": "search_done"},
        "analyze_resume": {"calling": "Analyzing",        "done": "analysis_done"},
        "parse_resume":   {"calling": "Parsing",          "done": "parse_done"},
    }

    @staticmethod
    def match_tool(name: str) -> dict | None:
        """模糊匹配 tool name，返回 TOOLS 中对应的配置"""
        if not name:
            return None
        lower = name.lower()
        for key, info in SSE.TOOLS.items():
            if key in lower:
                return info
        return None

    @staticmethod
    def state(**kw) -> str:
        return "[STATE]" + json.dumps(kw, ensure_ascii=False)


def sse_escape(text: str) -> str:
    return text.replace("\n", "\\n")
