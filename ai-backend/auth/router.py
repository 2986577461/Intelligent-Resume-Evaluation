"""本地邮箱注册、登录、登出。无需外部用户服务。"""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field

from common.security import require_same_origin

from auth.service import (
    SESSION_COOKIE, SESSION_TTL, authenticate, check_rate_limit, cookie_secure,
    create_session, get_session_user, register_user, revoke_session,
)


auth_router = APIRouter(prefix="/api/auth", tags=["账号"], dependencies=[Depends(require_same_origin)])


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(LoginRequest):
    password: str = Field(min_length=12, max_length=128)


def current_account(request: Request) -> dict:
    return get_session_user(request.cookies.get(SESSION_COOKIE, ""))


def _start_session(request: Request, response: Response, user: dict):
    # 轮换当前浏览器会话，旧 Cookie 即使被保存也不能继续使用。
    revoke_session(request.cookies.get(SESSION_COOKIE, ""))
    token = create_session(user["id"])
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True,
                        secure=cookie_secure(), samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"


@auth_router.post("/register", status_code=201, summary="邮箱注册并登录")
def register(body: RegisterRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    check_rate_limit("register-ip", ip, limit=5, window=3600)
    user = register_user(str(body.email), body.password)
    _start_session(request, response, user)
    return user


@auth_router.post("/login", summary="邮箱登录")
def login(body: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    check_rate_limit("login-ip", ip, limit=30, window=60)
    check_rate_limit("login-email", str(body.email).casefold(), limit=10, window=900)
    user = authenticate(str(body.email), body.password)
    _start_session(request, response, user)
    return user


@auth_router.get("/me", summary="当前登录账号")
def me(response: Response, user: dict = Depends(current_account)):
    response.headers["Cache-Control"] = "no-store"
    return user


@auth_router.post("/logout", status_code=204, summary="退出并撤销当前登录会话")
def logout(request: Request, response: Response):
    revoke_session(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                           secure=cookie_secure(), samesite="lax")
    response.headers["Cache-Control"] = "no-store"
