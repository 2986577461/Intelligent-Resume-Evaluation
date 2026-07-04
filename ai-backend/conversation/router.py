from fastapi import APIRouter, Depends, HTTPException
from conversation.service import get_current_user, get_app_db
from pydantic import BaseModel, Field


class CreateConversationRequest(BaseModel):
    title: str = Field(default="新的对话", description="会话标题")
    thread_id: str = Field(default="", description="会话 ID，不传则自动生成")


conversation_router = APIRouter(prefix="/api/conversations", tags=["会话管理"])


@conversation_router.get("", summary="获取会话列表")
def list_conversations(user_id: str = Depends(get_current_user)):
    """返回当前用户的会话，按更新时间倒序"""
    conn = get_app_db()
    try:
        rows = conn.execute(
            "SELECT thread_id, title, created_at, updated_at "
            "FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@conversation_router.delete("/{thread_id}", summary="删除会话")
def delete_conversation(thread_id: str, user_id: str = Depends(get_current_user)):
    """删除会话及其所有消息（仅允许删除自己的会话）"""
    conn = get_app_db()
    try:
        cur = conn.execute(
            "DELETE FROM conversations WHERE thread_id = ? AND user_id = ?", (thread_id, user_id)
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="会话不存在")
    finally:
        conn.close()


@conversation_router.get("/{thread_id}/messages", summary="获取会话消息")
def get_messages(thread_id: str, user_id: str = Depends(get_current_user)):
    """返回指定会话的所有消息，按时间正序"""
    conn = get_app_db()
    try:
        conv = conn.execute(
            "SELECT thread_id FROM conversations WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id)
        ).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")

        rows = conn.execute(
            "SELECT m.role, m.content, m.search_info, m.created_at, "
            "       ma.file_id, ma.filename AS file_filename "
            "FROM messages m "
            "LEFT JOIN message_attachments ma ON m.id = ma.message_id "
            "WHERE m.thread_id = ? "
            "ORDER BY m.id",
            (thread_id,),
        ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            fid = item.pop("file_id", None)
            fname = item.pop("file_filename", None)
            item["file"] = {"id": fid, "filename": fname} if fid else None
            result.append(item)
        return result
    finally:
        conn.close()