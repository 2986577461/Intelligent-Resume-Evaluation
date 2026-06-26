"""Agent 工具定义"""

import asyncio
from datetime import datetime

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from common.business_client import business_client
from files.store import get_user_file


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


@tool
def parse_resume_pdf(config: RunnableConfig) -> str:
    """解析用户上传的 PDF 简历文件，提取其中的文本内容。"""
    runtime = config.get("configurable", {})
    user_id = runtime.get("user_id", "")
    entry = get_user_file(user_id)
    if not entry:
        return "请先上传 PDF 简历文件。"
    return f"文件「{entry['name']}」的解析结果如下：\n\n{entry['text']}"