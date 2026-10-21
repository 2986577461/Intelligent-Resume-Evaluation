"""Agent 聊天 SSE 路由"""

from fastapi import APIRouter, Query, Depends, HTTPException
from fastapi.responses import StreamingResponse

from conversation.service import get_current_user, get_owned_file, require_thread_access
from agent.tools import get_current_time, list_resumes, parse_resume_by_index, analyze_resume

agent_router = APIRouter()


@agent_router.get("/chat-stream", summary="流式聊天 SSE（EventSource 用）")
async def chat_stream(question: str, thread_id: str = "default",
                      user_id: str = Depends(get_current_user),
                      file_id: str = Query(default="")):
    require_thread_access(thread_id, user_id)
    if file_id:
        uploaded = get_owned_file(file_id, user_id)
        if uploaded["thread_id"] != thread_id:
            raise HTTPException(404, "文件不存在")
    from agent.service import stream_chat
    from main import model, web_search
    return StreamingResponse(
        stream_chat(question, thread_id, user_id, file_id=file_id,
                    model=model,
                    tools=[get_current_time, web_search,
                           list_resumes, parse_resume_by_index,
                           analyze_resume]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )