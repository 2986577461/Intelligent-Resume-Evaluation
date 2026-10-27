"""PDF 阅读顺序回归：真实本地解析和上传，合成 PDF，不调用 OCR/模型服务。"""

import os
from pathlib import Path

import fitz
import pytest

from eval.test_auth import api, signup


def uploaded_text(api, raw):
    user = signup(api.client)
    response = api.client.post(
        "/api/files/upload", data={"thread_id": "reading-order"},
        files={"file": ("resume.pdf", raw, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return api.db.get_owned_file(response.json()["file_id"], user["id"])


def inline_code_pdf():
    with fitz.open() as doc:
        page = doc.new_page()
        # 先写正文，最后绘制不同字体的行内代码，模拟导出 PDF 的内容流顺序。
        page.insert_text((72, 72), "Backend internship", fontsize=11)
        page.insert_text((72, 100), "Clean up in", fontsize=11)
        page.insert_text((230, 100), "to prevent identity leaks.", fontsize=11)
        page.insert_text((72, 120), "Call", fontsize=11)
        page.insert_text((230, 120), "to publish an article.", fontsize=11)
        page.insert_text((72, 150), "NEXT SECTION", fontsize=11)
        page.insert_text((140, 100), "afterCompletion", fontname="cour", fontsize=8)
        page.insert_text((140, 120), "publish_article", fontname="cour", fontsize=8)
        return doc.tobytes()


def test_upload_reconstructs_inline_code_from_drawing_order(api):
    raw = inline_code_pdf()
    with fitz.open(stream=raw, filetype="pdf") as doc:
        unsorted = doc[0].get_text()
    # 确认样本确实包含旧路径的缺陷，而非只有正常 PDF 的成功测试。
    assert unsorted.index("NEXT SECTION") < unsorted.index("afterCompletion")
    row = uploaded_text(api, raw)
    lines = row["extracted_text"].splitlines()
    cleanup = next(line for line in lines if "Clean up in" in line)
    publish = next(line for line in lines if "to publish an article." in line)
    assert " ".join(cleanup.split()) == "Clean up in afterCompletion to prevent identity leaks."
    assert " ".join(publish.split()) == "Call publish_article to publish an article."
    assert row["extracted_text"].index("afterCompletion") < row["extracted_text"].index("NEXT SECTION")
    assert row["page_count"] == 1


def test_upload_keeps_explicit_page_boundary_and_all_text(api):
    with fitz.open() as doc:
        for text in ("PAGE_ONE_END", "PAGE_TWO_START"):
            page = doc.new_page()
            page.insert_text((72, 72), text)
        raw = doc.tobytes()
    row = uploaded_text(api, raw)
    assert row["page_count"] == 2
    assert row["extracted_text"].splitlines() == ["PAGE_ONE_END", "PAGE_TWO_START"]


def test_operator_pdf_inline_code_order_when_explicitly_supplied(api):
    # 显式授权才读取；私有文件只进入临时本地数据库，不入仓库、不外发。
    path = os.getenv("GUIDANCE_TARGET_TEST_PDF")
    if not path:
        pytest.skip("仅在操作者显式指定本地 PDF 时运行")
    row = uploaded_text(api, Path(path).read_bytes())
    lines = row["extracted_text"].splitlines()
    for term, before, after in (
        ("type:size:page", "文章分页缓存", "field"),
        ("afterCompletion", "ThreadLocal", "中清理"),
        ("publish_article", "Tool Call", "发送博客文章"),
    ):
        line = next(line for line in lines if term in line)
        assert line.index(before) < line.index(term) < line.index(after)
    stack_lines = [line for line in lines if "技术栈" in line and ("Langchain" in line or "Python" in line)]
    assert len(stack_lines) == 2
