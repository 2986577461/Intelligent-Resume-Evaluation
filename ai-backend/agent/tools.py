"""Agent 工具定义"""

import asyncio
from datetime import datetime

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from common.business_client import business_client
from common.logger import get_agent_logger
from conversation.service import get_app_db


# ── 公共辅助 ──


def _get_content_by_file_id_or_index(thread_id: str, file_id: str | None = None,
                                     index: int | None = None, user_id: str | None = None) -> dict | None:
    """查 uploaded_files，取第 index 份或按 file_id 查，返回 {file_id, filename, text}"""
    conn = get_app_db()

    if file_id:
        row = conn.execute(
            "SELECT file_id, filename, extracted_text FROM uploaded_files "
            "WHERE file_id = ?", (file_id,)
        ).fetchone()
        rows = [row] if row else []
        conn.close()
    else:
        rows = conn.execute(
            "SELECT file_id, filename, extracted_text FROM uploaded_files "
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

    return {"file_id": row["file_id"], "filename": row["filename"], "text": row["extracted_text"].strip()}


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


@tool
def analyze_resume(config: RunnableConfig, index: int | None = None, file_id: str | None = None,
                   position: str | None = None) -> str:
    """对简历进行多维度专业评分（技能、项目深度、经验、学历），返回json结构化评分报告。
    参数： index：文件的位置索引，从list_resumes工具中获取。 file_id:文件id，从上下文获取。
    position：仅在"上一次调用本工具提示简历缺少目标岗位、且你已经询问过用户"之后才传入用户回答的岗位名称；
    正常首次调用不要传这个参数。
你需要将返回的json按照json属性的顺序，美化格式后输出，禁止遗漏任何属性
当缺少position信息时，反问用户："简历上没有投递的岗位信息，请问你想投递什么岗位？"
分析完毕后，会返回四个模块的分数以及相关信息，禁止暴露每个模块的总分
"""
    import os
    runtime = config.get("configurable", {})
    user_id, thread_id = runtime.get("user_id", ""), runtime.get("thread_id", "")
    data = _get_content_by_file_id_or_index(thread_id, file_id, index, user_id)
    if not data:
        return "未找到该序号对应的简历文件。"

    try:
        from state.manager import get_cache_manager

        cache = get_cache_manager()

        from langchain.chat_models import init_chat_model
        from agent.graph import build_scoring_graph
        from agent.nodes import resume_analyst_node

        model = init_chat_model(model=os.getenv("MODEL_NAME", "deepseek-v4-flash"), max_tokens=2048)
        emit = runtime.get("emit_state")

        pending = cache.get_pending_evaluation(data["file_id"])
        if pending:
            extraction = pending
        else:
            extraction = resume_analyst_node({"resume_text": data["text"]}, model, emit)

        # 如果走的重试路线就将职位覆盖到简历中
        if position:
            extraction["position"] = position

        # 如果提取简历后发现缺少岗位
        if not extraction.get("position"):
            # 且缓存中也没有该简历
            if not pending:
                # 将缺少岗位的简历暂存入redis
                cache.set_pending_evaluation(data["file_id"], extraction)
            return "缺少position岗位信息"

        # 开始评估简历
        # 删除缓存
        if pending:
            cache.delete_pending_evaluation(data["file_id"])

        if emit:
            emit("简历信息评估中")
        graph = build_scoring_graph(model, emit)

        for s in graph.stream(extraction):
            node = s.get("report")
            if node:
                report = node.get("report", "") if isinstance(node, dict) else node
                if report:
                    return f"「{data['filename']}」的评分分析结果：\n\n{report}"
        return "评分分析失败，请重试。"
    except Exception as e:
        get_agent_logger().error(0, "analyze_resume_failed", "简历评分异常", str(e)[:300])
        return "简历分析失败，请稍后重试。"