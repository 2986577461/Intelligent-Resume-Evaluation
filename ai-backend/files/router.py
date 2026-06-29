"""文件上传 API 路由"""

import re
import uuid
from io import BytesIO

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import Response
from pypdf import PdfReader

from conversation.service import get_current_user, save_file_meta, get_file_content

files_router = APIRouter(prefix="/api/files", tags=["文件管理"])


def _clean_pdf_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


@files_router.post("/upload", summary="上传 PDF 文件")
async def upload_file(file: UploadFile = File(...),
                      thread_id: str =Form(),
                      user_id: str = Depends(get_current_user)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件内容为空")

    try:
        reader = PdfReader(BytesIO(raw))
        text = "".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        raise HTTPException(400, f"PDF 解析失败：{e}")

    text = _clean_pdf_text(text)
    if not text:
        raise HTTPException(400, "未能从 PDF 中提取到文本内容")

    file_id = uuid.uuid4().hex[:12]

    save_file_meta(file_id, file.filename, len(text),thread_id, user_id, content=raw)

    return {"file_id": file_id, "filename": file.filename}


@files_router.get("/{file_id}", summary="获取文件原始内容")
def get_file(file_id: str):
    content = get_file_content(file_id)
    if content is None:
        raise HTTPException(404, "文件不存在")
    return Response(content=content, media_type="application/pdf")