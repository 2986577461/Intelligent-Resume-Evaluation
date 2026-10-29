"""Agent 聊天 SSE 路由"""

from fastapi import APIRouter, Query, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from agent.discussion import DiscussionContext

from conversation.service import get_current_user, get_owned_file, require_thread_access
from agent.tools import get_current_time, list_resumes, parse_resume_by_index, analyze_resume

agent_router = APIRouter()


@agent_router.get("/chat-stream", summary="流式聊天 SSE（EventSource 用）")
async def chat_stream(question: str, thread_id: str = "default",
                      user_id: str = Depends(get_current_user),
                      file_id: str = Query(default=""),
                      discussion_context: str = Query(default="", max_length=12000)):
    require_thread_access(thread_id, user_id)
    discussion = None
    if discussion_context:
        try:
            discussion = DiscussionContext.model_validate_json(discussion_context)
        except ValidationError:
            raise HTTPException(422, "关联建议格式不正确")
        if discussion.file_id:
            original = get_owned_file(discussion.file_id, user_id)
            if original["thread_id"] != thread_id:
                raise HTTPException(404, "文件不存在")
    if file_id:
        uploaded = get_owned_file(file_id, user_id)
        if uploaded["thread_id"] != thread_id:
            raise HTTPException(404, "文件不存在")
    from agent.service import stream_chat
    from main import model, web_search
    return StreamingResponse(
        stream_chat(question, thread_id, user_id, file_id=file_id, discussion=discussion,
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