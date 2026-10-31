"""会话 cookie 解析：登录用户优先，游客兜底。

登录用户的 cookie 持久化在 users 表（见 login.py），此处按
「当前登录用户 → 游客单例」的链路返回；工具层无感知。
"""

import logging
import threading

import login
import requests

from config import get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cookie: str | None = None


class GuestLoginError(RuntimeError):
    """游客登录失败（music-api 未启动、接口异常或触发频控）。"""


def get_or_create_cookie(session_id: str) -> str:
    """会话 cookie 解析链：当前登录用户 → 游客兜底。

    用户扫码登录后，cookie 持久化在 users 表（login.get_current_user），
    这里优先返回；未登录或查询异常时走游客单例。
    """
    try:
        user = login.get_current_user()
    except Exception as e:
        logger.warning("查询登录用户异常，走游客兜底: %s", e)
        user = None
    if user and user.get("cookie"):
        return user["cookie"]

    return _get_or_create_guest_cookie(session_id)


def _get_or_create_guest_cookie(session_id: str) -> str:
    """游客 cookie 单例（原有逻辑）。"""
    global _cookie
    if _cookie:
        return _cookie

    with _lock:
        if _cookie:  # 双重检查：并发请求下也只注册一次
            return _cookie

        settings = get_settings()
        url = f"{settings.music_api_base_url}/register/anonimous"
        try:
            response = requests.post(url, timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            raise GuestLoginError(
                f"游客登录失败，无法连接 music-api（{settings.music_api_base_url}），"
                f"请确认服务已启动。原因：{e}"
            ) from e

        cookie = data.get("cookie", "")
        if data.get("code") != 200 or not cookie:
            raise GuestLoginError(
                f"游客登录接口返回异常：code={data.get('code')}"
                f"（可能触发了网易对游客注册的频控，请稍后重试）"
            )

        _cookie = cookie
        logger.info("已注册全局游客登录态 [session=%s]", session_id[:8])
        return _cookie


def invalidate_cookie() -> None:
    """丢弃全局游客登录态，下次调用将重新注册。

    预留给 cookie 过期/失效场景：工具层检测到登录失效时调用。
    """
    global _cookie
    _cookie = None
