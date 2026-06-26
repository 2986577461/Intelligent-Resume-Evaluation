"""文件上传 API 路由"""

import re
import uuid
from io import BytesIO

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import Response
from pypdf import PdfReader

from files.store import set_user_file
from conversation.service import get_current_user, save_file_meta, get_file_content
from kb.tools import _chunk_text
from common.vector_store import get_vector_store

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

    # 1. 内存：最新文件给 agent tool 用
    set_user_file(user_id, file_id, file.filename, text)

    # 2. SQLite：持久化文件元数据 + 原始内容
    chunks = _chunk_text(text, chunk_size=400, overlap=60)
    save_file_meta(file_id, file.filename, len(text), len(chunks), user_id, content=raw)

    # 3. Milvus：向量化（失败不影响上传）
    try:
        doc_id = int(file_id, 16) % (2**63)
        get_vector_store().insert_chunks(doc_id, chunks, user_id)
    except Exception:
        pass

    return {"file_id": file_id, "filename": file.filename}


@files_router.get("/{file_id}", summary="获取文件原始内容")
def get_file(file_id: str, user_id: str = Depends(get_current_user)):
    content = get_file_content(file_id)
    if content is None:
        raise HTTPException(404, "文件不存在")
    return Response(content=content, media_type="application/pdf")
