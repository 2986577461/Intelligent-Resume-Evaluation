"""Agent 聊天 SSE 路由"""

from fastapi import APIRouter, Query, Depends, HTTPException
from fastapi.responses import StreamingResponse

from conversation.service import get_current_user, get_owned_file, require_thread_access
from agent.tools import get_current_time, list_resumes, parse_resume_by_index, analyze_resume
from agent.schema import AnalyzeRequest, GuidanceResponse, Result

agent_router = APIRouter()


@agent_router.post("/api/resume-guidance", response_model=GuidanceResponse, summary="基于确认岗位生成有来源的简历指导")
def resume_guidance(request: AnalyzeRequest, user_id: str = Depends(get_current_user)):
    from agent.guidance_service import run_guidance
    from conversation.service import (ensure_conversation_and_save_user, save_ai_message,
                                      save_message_attachment)
    # 校验必须发生在保存任何消息、调用模型之前。
    require_thread_access(request.thread_id, user_id)
    uploaded = get_owned_file(request.file_id, user_id)
    if uploaded["thread_id"] != request.thread_id:
        raise HTTPException(404, "文件不存在")
    result, markdown = run_guidance(request, user_id)
    question = ("补充简历信息：" + "；".join(a.text for a in request.answers)) if request.answers else f"简历指导，确认目标岗位：{request.target_role or '待确认'}"
    thread_id, message_id = ensure_conversation_and_save_user(request.thread_id, question, user_id)
    save_message_attachment(message_id, request.file_id, uploaded["filename"])
    save_ai_message(thread_id, markdown, {"guidance": result.model_dump()}, user_id)
    return {"result": result.model_dump(), "markdown": markdown, "user_message": question}


@agent_router.get("/api/resume-guidance/{file_id}/{result_id}", response_model=Result, summary="读取所属文件的结构化指导")
def get_guidance(file_id: str, result_id: str, thread_id: str,
                 user_id: str = Depends(get_current_user)):
    from state.manager import GuidanceStore
    return GuidanceStore().result(result_id, user_id, thread_id, file_id)


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