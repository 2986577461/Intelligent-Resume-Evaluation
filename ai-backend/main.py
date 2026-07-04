"""
协会网站智能聊天机器人 —— 应用入口

启动方式：python3 main.py
"""

import asyncio
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from langchain.chat_models import init_chat_model
from langchain_tavily import TavilySearch

load_dotenv()

# ---- 导入路由 ----
from conversation.router import conversation_router
from agent.router import agent_router
from files.router import files_router


async def _cleanup_orphaned_files():
    """每小时清理未绑定消息且超过 1 小时的孤立上传文件"""
    from conversation.service import get_app_db
    while True:
        await asyncio.sleep(3600)
        try:
            conn = get_app_db()
            conn.execute("""
                DELETE FROM uploaded_files
                WHERE file_id NOT IN (SELECT file_id FROM message_attachments)
                  AND created_at < datetime('now', '-1 hour')
            """)
            conn.commit()
            conn.close()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_orphaned_files())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


model = init_chat_model(model="deepseek-v4-flash", max_tokens=4096)

web_search = TavilySearch(
    max_results=3,
    description="用于搜索实时信息",
)


app = FastAPI(
    lifespan=lifespan,
    title="智能简历评估",
    description="支持联网搜索、SQLite 记忆、流式输出的智能对话接口",
    version="1.0.0",
)

app.include_router(conversation_router)
app.include_router(agent_router)
app.include_router(files_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get("/", response_class=FileResponse)
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "frontend.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="warning",
    )