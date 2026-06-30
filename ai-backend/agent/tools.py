"""Agent 工具定义"""

import asyncio
from datetime import datetime
from io import BytesIO

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pypdf import PdfReader

from common.business_client import business_client
from conversation.service import get_app_db


# ── 公共辅助 ──


def _get_content_by_file_id_or_index(thread_id: str, file_id: str | None = None,
                                     index: int | None = None, user_id: str | None = None) -> dict | None:
    """查 uploaded_files，取第 index 份或按 file_id 查，解析 content BLOB 返回 {file_id, filename, text}"""
    conn = get_app_db()

    if file_id:
        row = conn.execute(
            "SELECT file_id, filename, content FROM uploaded_files "
            "WHERE file_id = ?", (file_id,)
        ).fetchone()
        rows = [row] if row else []
    else:
        rows = conn.execute(
            "SELECT file_id, filename, content FROM uploaded_files "
            "WHERE user_id = ? AND thread_id = ? ORDER BY created_at",
            (user_id, thread_id),
        ).fetchall()
        conn.close()

    if file_id:
        row = rows[0] if rows else None
    else:
        if not rows or index < 1 or index > len(rows):
            return None
        row = rows[index - 1]

    if not row or not row["content"]:
        return None

    text = "".join(page.extract_text() or "" for page in PdfReader(BytesIO(row["content"])).pages)
    if not text.strip():
        return None

    return {"file_id": row["file_id"], "filename": row["filename"], "text": text.strip()}


def _get_user_thread(config: RunnableConfig) -> tuple[str, str]:
    """从 config 提取 user_id 和 thread_id"""
    runtime = config.get("configurable", {})
    return runtime.get("user_id", ""), runtime.get("thread_id", "")


# ── 业务工具 ──

@tool
def get_user_identity(config: RunnableConfig) -> str:
    """从协会业务系统获取当前登录用户的信息"""
    runtime = config.get("configurable", {})
    token = runtime.get("token", "")
    if not token:
        return "未登录，无法获取用户身份"

    async def _fetch():
        return await business_client.get_user_info(token)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_fetch())
    finally:
        loop.close()
    if result.get("code") == "200":
        return str(result.get("data", {}))
    return f"获取用户信息失败: {result.get('msg', '未知错误')}"


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
            "SELECT file_id, filename FROM uploaded_files "
            "WHERE user_id = ? AND thread_id = ? ORDER BY created_at",
            (user_id, thread_id),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return "你还未上传过任何简历文件。"

    lines = [f"{i + 1}. {r['filename']}" for i, r in enumerate(rows)]
    return f"你共有 {len(rows)} 份简历：\n" + "\n".join(lines)


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


@tool
def analyze_resume(config: RunnableConfig, index: int | None = None, file_id: str | None = None) -> str:
    """对简历进行多维度专业评分（技能、项目深度、经验、学历），返回json结构化评分报告。
    参数： index：文件的位置索引，从list_resumes工具中获取。 file_id:文件id，从上下文获取。
    返回的json结构化评分报告格式如下：
    {
  "overall_score": 总分,
  "dimensions": {
    "skill_match": {"score": 分数, "detail": ["评分理由"]},
    "project_depth": {"score": 分数, "detail": ["评分理由"]},
    "experience": {"score": 分数, "detail": ["评分理由"]},
    "education": {"score": 分数, "detail": ["评分理由"]}
  },
  "strengths": ["亮点1", "亮点2"],
  "risks": ["风险/改进点1"],
  "interview_questions": ["建议面试问题1"],
  "summary": "总结"
}
其中技能匹配满分10分，项目深度满分35分，经历满分20分，教育35分
你需要将以上json按照json属性的顺序，美化格式后输出，禁止遗漏任何属性
当用户要求'分析''评价''打分''评估'时用这个。"""
    import os
    user_id, thread_id = _get_user_thread(config)
    data = _get_content_by_file_id_or_index(thread_id, file_id, index, user_id)
    if not data:
        return "未找到该序号对应的简历文件。"

    from langchain.chat_models import init_chat_model
    from agent.graph import build_eval_graph
    model = init_chat_model(model=os.getenv("MODEL_NAME", "deepseek-v4-flash"))
    graph = build_eval_graph(model)

    for s in graph.stream({"resume_text": data["text"]}):
        if "report" in s:
            report = s["report"].get("report", "")
            if report:
                return f"「{data['filename']}」的评分分析结果：\n\n{report}"
    return "评分分析失败，请重试。"