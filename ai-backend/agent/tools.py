"""Agent 工具定义"""

from datetime import datetime

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from common.logger import get_agent_logger
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
    """提取并保留简历事实，提示用户通过页面确认岗位后分析，不猜测目标岗位。
    index 从 list_resumes 获取，或使用用户消息中的 file_id。岗位和 JD 由页面显式确认。
    """
    runtime = config.get("configurable", {})
    user_id, thread_id = runtime.get("user_id", ""), runtime.get("thread_id", "")
    data = _get_content_by_file_id_or_index(thread_id, file_id, index, user_id)
    if not data:
        return "未找到该序号对应的简历文件。"

    try:
        from agent.guidance_service import run_guidance
        from agent.schema import AnalyzeRequest
        result, report = run_guidance(AnalyzeRequest(thread_id=thread_id, file_id=data["file_id"]),
                                      user_id, runtime.get("emit_state"))
        if runtime.get("emit_guidance"):
            runtime["emit_guidance"](result.model_dump())
        return report + "\n\n请在页面的「简历指导」中选择此文件、确认目标岗位（可选填写 JD）后继续。"
    except Exception as e:
        get_agent_logger().error(0, "analyze_resume_failed", "简历指导异常", type(e).__name__)
        return "简历分析失败，请稍后重试。"
