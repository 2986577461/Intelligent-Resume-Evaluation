"""生产入口适配：单实例并发上限；模型只在真实执行时初始化。"""
import os
import threading

from fastapi import HTTPException

from agent.graph import analyze
from agent.nodes import Budget, ProcessingError
from agent.render import present
from agent.schema import AnalyzeRequest, ProcessingFailure
from state.manager import GuidanceStore

_WORKERS = threading.BoundedSemaphore(2)


def new_model():
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        raise ProcessingError("configuration", ProcessingFailure(stage="model_init", exception_type="MissingAPIKey",
                                                               attempts=0))
    # 项目 OCR/示例配置使用 BASE_URL，LangChain 原生使用 API_BASE；两种都支持。
    # 显式传入，避免已配置代理网关却误请求官方地址。
    base_url = (os.getenv("DEEPSEEK_BASE_URL") or os.getenv("DEEPSEEK_API_BASE") or "https://api.deepseek.com/v1").rstrip("/")
    from langchain.chat_models import init_chat_model
    return init_chat_model(model=os.getenv("MODEL_NAME", "deepseek-v4-flash"), model_provider="deepseek",
                           base_url=base_url, max_tokens=6000,
                           model_kwargs={"response_format": {"type": "json_object"}},
                           # 该链路需要直接的结构化答案，不使用默认思考模式消耗延迟/输出预算。
                           extra_body={"thinking": {"type": "disabled"}},
                           timeout=float(os.getenv("GUIDANCE_REQUEST_TIMEOUT_SECONDS", "25")), max_retries=0)


def run_guidance(request: AnalyzeRequest, user_id, emit=None):
    if not _WORKERS.acquire(blocking=False):
        raise HTTPException(429, "简历分析繁忙，请稍后重试")
    try:
        result = analyze(request, user_id, GuidanceStore(), new_model, Budget(), emit)
        return result, present(result)
    finally:
        # 只有底层请求实际返回才释放；客户端断开不代表底层已取消。
        _WORKERS.release()
