"""文件上传 API 路由"""

import re
import uuid

import fitz
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import Response

from conversation.service import get_current_user, save_file_meta, get_file_content, get_filename, get_app_db

files_router = APIRouter(prefix="/api/files", tags=["文件管理"])

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MIME_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _clean_pdf_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _ext(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@files_router.post("/upload", summary="上传简历文件（PDF / 图片）")
async def upload_file(file: UploadFile = File(...),
                      thread_id: str = Form(),
                      user_id: str = Depends(get_current_user)):
    ext = _ext(file.filename or "")
    if ext not in (".pdf", *IMAGE_EXTS):
        raise HTTPException(400, "仅支持 PDF 或图片（png/jpg/jpeg/webp）")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件内容为空")

    if ext == ".pdf":
        try:
            doc = fitz.open(stream=raw, filetype="pdf")
            page_count = len(doc)
            text = "".join(page.get_text() for page in doc)
            doc.close()
        except Exception as e:
            raise HTTPException(400, f"PDF 解析失败：{e}")
        text = _clean_pdf_text(text)
    else:
        page_count = 1
        from common.vision_client import extract_text_from_image
        try:
            text = extract_text_from_image(raw, MIME_MAP.get(ext, "image/png"))
        except Exception as e:
            raise HTTPException(400, f"图片识别失败：{e}")

    if not text.strip():
        raise HTTPException(400, "未能从文件中提取到文本内容")

    file_id = uuid.uuid4().hex[:12]
    save_file_meta(file_id, file.filename, len(text), thread_id, user_id,
                   content=raw, extracted_text=text.strip(), page_count=page_count)

    return {"file_id": file_id, "filename": file.filename}


@files_router.delete("/orphaned", summary="清理当前用户未绑定消息的孤立文件")
async def cleanup_orphaned(user_id: str = Depends(get_current_user)):
    """页面加载时调用，删除该用户上传超过1分钟但未绑定任何消息的文件"""
    conn = get_app_db()
    try:
        conn.execute("""
            DELETE FROM uploaded_files
            WHERE user_id = ?
              AND file_id NOT IN (SELECT file_id FROM message_attachments)
              AND created_at < datetime('now', '-1 minute')
        """, (user_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@files_router.get("/{file_id}", summary="获取文件原始内容")
def get_file(file_id: str):
    content = get_file_content(file_id)
    if content is None:
        raise HTTPException(404, "文件不存在")
    filename = get_filename(file_id) or ""
    media_type = MIME_MAP.get(_ext(filename), "application/octet-stream")
    return Response(content=content, media_type=media_type)


@files_router.delete("/{file_id}", summary="删除未绑定消息的文件")
def delete_file(file_id: str):
    conn = get_app_db()
    try:
        linked = conn.execute(
            "SELECT 1 FROM message_attachments WHERE file_id = ?", (file_id,)
        ).fetchone()
        if linked:
            raise HTTPException(409, "文件已绑定消息，无法删除")
        conn.execute("DELETE FROM uploaded_files WHERE file_id = ?", (file_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}
