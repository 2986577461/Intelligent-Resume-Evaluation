"""Agent 工具定义"""

from datetime import datetime

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from conversation.service import get_app_db


# ── 公共辅助 ──


def _get_content_by_file_id_or_index(thread_id: str, file_id: str | None = None,
                                     index: int | None = None, user_id: str | None = None) -> dict | None:
    """查 uploaded_files，取第 index 份或按 file_id 查，返回 {file_id, filename, text}"""
    conn = get_app_db()

    if file_id:
        row = conn.execute(
            "SELECT file_id, filename, extracted_text, page_count FROM uploaded_files "
            "WHERE file_id = ? AND user_id = ? AND thread_id = ?", (file_id, user_id, thread_id)
        ).fetchone()
        rows = [row] if row else []
        conn.close()
    else:
        rows = conn.execute(
            "SELECT file_id, filename, extracted_text, page_count FROM uploaded_files "
            "WHERE user_id = ? AND thread_id = ? ORDER BY created_at",
            (user_id, thread_id),
        ).fetchall()
        conn.close()

    if file_id:
        row = rows[0] if rows else None
    else:
        if not rows or index is None or index < 1 or index > len(rows):
            return None
        row = rows[index - 1]

    if not row or not row["extracted_text"]:
        return None

    return {"file_id": row["file_id"], "filename": row["filename"], "text": row["extracted_text"].strip(),
            "page_count": row["page_count"] or 1}


def _get_user_thread(config: RunnableConfig) -> tuple[str, str]:
    """从 config 提取 user_id 和 thread_id"""
    runtime = config.get("configurable", {})
    return runtime.get("user_id", ""), runtime.get("thread_id", "")


# ── 业务工具 ──

@tool
def get_current_time() -> str:
    """获取当前系统时间，返回 ISO 格式的日期时间字符串。"""
    return datetime.now().isoformat()


# ── 简历工具 ──

@tool
def list_resumes(config: RunnableConfig) -> str:
    """列出当前会话中用户上传的所有简历文件及其索引。"""
    user_id, thread_id = _get_user_thread(config)
    conn = get_app_db()
    try:
        rows = conn.execute(
            "SELECT filename FROM uploaded_files "
            "WHERE user_id = ? AND thread_id = ? ORDER BY created_at",
            (user_id, thread_id),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return "你还未上传过任何简历文件。"

    lines = [f"{i + 1}. {r['filename']}" for i, r in enumerate(rows)]
    return f"共有 {len(rows)} 份简历：\n" + "\n".join(lines)


@tool
def parse_resume_by_index(config: RunnableConfig, index: int | None = None, file_id: str | None = None) -> str:
    """仅获取简历原文，不做任何评分或分析。
    参数： index：文件的位置索引，从list_resumes工具中获取。 file_id:文件id，从上下文获取。
当你只需要阅读简历内容、让用户自己判断，或者用户只是说'看看''读一下'时用这个。"""
    user_id, thread_id = _get_user_thread(config)
    data = _get_content_by_file_id_or_index(thread_id, file_id, index, user_id)
    if not data:
        return "未找到该序号对应的简历文件。"
    return f"文件「{data['filename']}」的内容如下：\n\n{data['text']}"


@tool(return_direct=True)
def analyze_resume(config: RunnableConfig, index: int | None = None, file_id: str | None = None) -> str:
    """解析简历，给出最多3条有依据的优先优化建议和最多5个具体面试追问，不评分。
    用户请求完整分析、评价、优化简历或补充上一轮待确认投递信息时使用。
    已有报告的解释、局部讨论或普通面试知识问答，不默认调用本工具重跑。
    单纯问“校招是什么意思”等概念、取消或拒绝继续，不是补充投递信息。
    有 file_id 时直接使用；无附件且正在补充投递信息时可以省略 file_id 和 index，
    系统会恢复本会话暂存的文件。其他无附件场景先 list_resumes 获取索引。
    目标岗位、实习/校招/社招和可选 JD 从本轮原始消息识别，不要在解析前强制询问。
    """
    from agent.scoring import create_scoring_model, evaluate_resume
    from agent.telemetry import EvaluationTrace
    from state.manager import get_cache_manager

    from agent.guidance_state import GuidanceStore
    store = GuidanceStore()
    user_id, thread_id = _get_user_thread(config)
    runtime = config.get("configurable", {})
    trace = EvaluationTrace(runtime.get("record_metric"))
    try:
        # 本轮新附件优先；否则恢复当前文件，不依赖模型记住文件 ID。
        file_id = runtime.get("file_id") or file_id
        if not file_id and index is None:
            pending = store.load(user_id, thread_id)
            file_id = pending["file_id"] if pending else None
        data = _get_content_by_file_id_or_index(thread_id, file_id, index, user_id)
        if not data:
            return "未找到对应的简历文件，请上传简历或指定本会话中的文件。"
        model = create_scoring_model()
        try:
            cache = get_cache_manager()
        except Exception as error:
            trace.record("cache_error", operation="initialize", error_type=type(error).__name__)
            cache = None
        return evaluate_resume(data, user_id, thread_id, model, cache,
                               user_message=runtime.get("user_message", ""), store=store,
                               emit_state=runtime.get("emit_state"), emit_chunk=runtime.get("emit_chunk"), trace=trace)
    except Exception as error:
        trace.record("failure", error_type=type(error).__name__)
        trace.finish("failed")
        return "简历指导生成失败，请稍后重试。"
