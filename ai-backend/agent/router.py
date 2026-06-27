"""Agent 聊天 SSE 路由"""

from fastapi import APIRouter, Query, Depends
from fastapi.responses import StreamingResponse

from conversation.service import get_current_user
from agent.service import stream_chat
from agent.tools import get_current_time, get_user_identity, list_resumes, parse_resume_by_index, analyze_resume

agent_router = APIRouter()


@agent_router.get("/chat-stream", summary="流式聊天 SSE（EventSource 用）")
async def chat_stream(question: str, thread_id: str = "default",
                      user_id: str = Depends(get_current_user),
                      token: str = Query(default=""),
                      file_id: str = Query(default="")):
    from main import model, web_search
    return StreamingResponse(
        stream_chat(question, thread_id, user_id, token, file_id=file_id,
                    model=model,
                    tools=[get_current_time, web_search,
                           get_user_identity,
                           list_resumes, parse_resume_by_index,
                           analyze_resume]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )