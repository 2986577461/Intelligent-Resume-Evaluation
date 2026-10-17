"""同源 Cookie 请求保护；不依赖第三方认证服务。"""

import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def require_same_origin(request: Request):
    # Production is reachable only through trusted Docker-network proxies. The TLS
    # gateway overwrites this header; the legacy HTTP proxy sends "http".
    if os.getenv("REQUIRE_HTTPS", "false").lower() == "true":
        if request.headers.get("x-forwarded-proto") != "https":
            raise HTTPException(400, "请使用 HTTPS 地址访问")
    # SameSite Cookie 是第一道防线，额外拒绝跨站请求和异源 Origin。
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站请求")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "不允许跨域请求")
