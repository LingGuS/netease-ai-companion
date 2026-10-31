"""扫码登录流程管理（全局当前用户模型）。

流程：start_qr_login 生成二维码 → 用户扫码 → check_qr_login 轮询 →
803 授权成功时把 cookie 持久化到 users 表（长期存储，防频繁登录风控）。
之后所有请求的 cookie 解析链优先使用当前登录用户（见 session.py）。

安全约束：cookie 只进 config 通道与 users 表，绝不出现在任何 API 响应里。
"""

import logging
import time
import uuid

import db
from config import get_settings
from music_api import call_music_api

logger = logging.getLogger(__name__)

# login_id -> {"key": unikey, "created_at": epoch}
_qr_logins: dict[str, dict] = {}
_LOGIN_TTL = 300  # 二维码有效期 5 分钟（与网易侧一致）


class QRLoginError(RuntimeError):
    """二维码登录流程异常。"""


def _cleanup_expired() -> None:
    now = time.time()
    for k in [k for k, v in _qr_logins.items() if now - v["created_at"] > _LOGIN_TTL]:
        _qr_logins.pop(k, None)


def start_qr_login() -> dict:
    """生成登录二维码：key → base64 图片；返回 login_id 供轮询。"""
    _cleanup_expired()
    ts = int(time.time() * 1000)

    key_resp = call_music_api("/login/qr/key", cookie="", data={"timestamp": ts})
    unikey = (key_resp.get("data") or {}).get("unikey")
    if not unikey:
        raise QRLoginError(f"获取二维码 key 失败：{key_resp.get('code')}")

    create_resp = call_music_api(
        "/login/qr/create",
        cookie="",
        data={"key": unikey, "qrimg": "true", "timestamp": ts},
    )
    qrimg = (create_resp.get("data") or {}).get("qrimg")
    if not qrimg:
        raise QRLoginError(f"生成二维码失败：{create_resp.get('code')}")

    login_id = uuid.uuid4().hex
    _qr_logins[login_id] = {"key": unikey, "created_at": time.time()}
    logger.info("已生成登录二维码 [login=%s]", login_id[:8])
    return {"login_id": login_id, "qrimg": qrimg}


def check_qr_login(login_id: str) -> dict:
    """轮询扫码状态；803 时持久化用户并返回 success。"""
    entry = _qr_logins.get(login_id)
    if not entry:
        return {"status": "expired", "message": "登录会话不存在或已过期，请刷新二维码"}
    if time.time() - entry["created_at"] > _LOGIN_TTL:
        _qr_logins.pop(login_id, None)
        return {"status": "expired", "message": "二维码已过期，请刷新"}

    key = entry["key"]
    ts = int(time.time() * 1000)
    data = call_music_api("/login/qr/check", cookie="", data={"key": key, "timestamp": ts})
    if not data.get("code"):
        # 空响应（502 场景）：按网易文档带 noCookie 参数重试一次
        data = call_music_api(
            "/login/qr/check", cookie="", data={"key": key, "timestamp": ts, "noCookie": "true"}
        )

    code = data.get("code")
    if code == 803:
        cookie = data.get("cookie") or ""
        if not cookie:
            return {"status": "failed", "message": "授权成功但未返回登录态，请重试"}
        user = _save_login_user(cookie)
        _qr_logins.pop(login_id, None)
        return {"status": "success", "message": "登录成功", "user": user}

    status_map = {800: "expired", 801: "waiting", 802: "confirmed"}
    message_map = {
        800: "二维码已过期，请刷新",
        801: "等待扫码",
        802: "已扫码，请在手机上确认",
    }
    return {
        "status": status_map.get(code, "waiting"),
        "message": message_map.get(code, "等待扫码"),
    }


def _save_login_user(cookie: str) -> dict:
    """用登录 cookie 拉取账号信息并写入 users 表（长期存储）。"""
    account = call_music_api("/user/account", cookie)
    profile = (account.get("profile") or {}) if account.get("code") == 200 else {}
    uid = profile.get("userId")
    if not uid:
        raise QRLoginError("授权成功但无法读取账号信息，请稍后重试")

    row = {
        "netease_uid": uid,
        "nickname": profile.get("nickname"),
        "vip_type": profile.get("vipType", 0),
        "cookie": cookie,
    }
    with db.get_conn() as conn:
        db.upsert_user(conn, row)
    logger.info("用户 %s(%s) 登录成功，cookie 已持久化", profile.get("nickname"), uid)
    return {"netease_uid": uid, "nickname": profile.get("nickname"), "vip_type": row["vip_type"]}


def get_current_user() -> dict | None:
    """全局当前登录用户（users 表最近有有效 cookie 者）；未登录返回 None。"""
    try:
        with db.get_conn() as conn:
            return db.get_current_user(conn)
    except Exception as e:
        logger.warning("查询当前登录用户失败: %s", e)
        return None
