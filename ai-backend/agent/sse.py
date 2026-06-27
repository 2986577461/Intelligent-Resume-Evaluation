"""SSE 信号常量与工具函数"""

import json


class SSE:
    DONE = "[DONE]"
    ERROR = "[ERROR]"
    GENERATING = "generating"
    THINKING = "thinking"
    ANALYZING = "analyzing"
    SCORING = "scoring"

    @staticmethod
    def state(**kw) -> str:
        return "[STATE]" + json.dumps(kw, ensure_ascii=False)


def sse_escape(text: str) -> str:
    return text.replace("\n", "\\n")
