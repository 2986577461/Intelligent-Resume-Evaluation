"""MiMo 视觉模型客户端 — 图片简历 OCR 为文本（OpenAI 兼容接口）"""

import base64
import os

import httpx

MIMO_BASE_URL = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
MIMO_API_KEY = os.getenv("xiaomi_api_key", "")
MIMO_VISION_MODEL = os.getenv("MIMO_VISION_MODEL", "mimo-v2.5")

_OCR_PROMPT = "提取这张简历图片中的所有文字，按原始排版顺序输出纯文本，不要添加任何解释或额外内容。"


def extract_text_from_image(image_bytes: bytes, mime: str = "image/png") -> str:
    """调用 MiMo 视觉模型，从图片中提取文本"""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    payload = {
        "model": MIMO_VISION_MODEL,
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
        "Authorization": f"Bearer {MIMO_API_KEY}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=60) as client:
        resp = client.post(f"{MIMO_BASE_URL}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"].strip()
