"""画像类工具：听歌品味分析（数据经 profile.py 计算）。"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from db import get_conn, get_profile
from music_api import get_cookie_from_config
from store import get_uid
from taste_profile import ensure_profile

_GUEST_HINT = (
    "获取失败：当前是游客身份，没有听歌数据，无法分析品味。"
    "需要登录真实账号后才能使用此功能，可以引导用户稍后再试。"
)


@tool
def analyze_my_taste(force: bool = False, config: RunnableConfig = None) -> str:
    """
    分析当前用户的听歌品味与音乐画像（基于真实听歌排行数据归纳）。
    当用户问"分析一下我的听歌风格"、"我是什么品味"、"我喜欢什么类型的音乐"时，请调用此工具。
    返回画像描述、偏好流派与常听歌手，可直接组织成回答。
    force 参数：默认 false 用 24 小时内的缓存画像；传 true 强制重新计算。
    (注意：需要真实账号登录，游客身份无法分析)
    """
    cookie = get_cookie_from_config(config)

    try:
        uid = get_uid(cookie)
        if not uid:
            return _GUEST_HINT
        profile = ensure_profile(uid, cookie, force=force)
        if not profile:
            return "暂无画像数据。"
        with get_conn() as conn:
            fresh = get_profile(conn, uid)
    except Exception as e:
        return f"画像分析失败（数据层）：{e}"

    profile_text = fresh.get("profile_text") or ""
    genres = fresh.get("favorite_genres") or {}
    artists = fresh.get("favorite_artists") or []

    genre_str = "、".join(f"{k}({v})" for k, v in list(genres.items())[:5]) or "暂无"
    artist_str = "、".join(artists[:10]) or "暂无"
    return (
        f"用户音乐画像分析完成：\n"
        f"- 画像描述：{profile_text}\n"
        f"- 偏好流派（权重）：{genre_str}\n"
        f"- 常听歌手 Top10：{artist_str}\n"
        f"（可基于此自然地回答用户的品味问题，并可在推荐时贴合该画像）"
    )
