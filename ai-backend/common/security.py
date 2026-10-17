"""同源 Cookie 请求保护；不依赖第三方认证服务。"""

from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def require_same_origin(request: Request):
    # SameSite Cookie 是第一道防线，额外拒绝跨站请求和异源 Origin。
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站请求")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "不允许跨域请求")
