"""DeepSeek 视觉模型客户端 — 图片简历 OCR 为文本（OpenAI 兼容接口）"""

import base64
import os

import httpx

from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_VISION_MODEL = os.getenv(
    "DEEPSEEK_VISION_MODEL", "deepseek-v4-flash-vision-exp"
)

_OCR_PROMPT = "提取这张简历图片中的所有文字，按原始排版顺序输出纯文本，不要添加任何解释或额外内容。"


def extract_text_from_image(image_bytes: bytes, mime: str = "image/png") -> str:
    """调用 DeepSeek 视觉模型，从图片中提取文本。"""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    payload = {
        "model": DEEPSEEK_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"].strip()
