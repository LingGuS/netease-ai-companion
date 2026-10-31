"""推荐类工具：每日推荐、推荐歌单、官方榜单（结果歌曲经 store 落库沉淀）。"""

import logging

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from music_api import MusicAPIError, call_music_api, format_song, get_cookie_from_config
from store import cache_songs

logger = logging.getLogger(__name__)


@tool
def get_daily_recommend_songs(limit: int = 10, config: RunnableConfig = None) -> str:
    """
    获取网易云音乐为当前用户生成的每日推荐歌曲。
    当用户说"推荐几首歌"、"今天听什么"、"来点音乐"时，优先调用此工具。
    返回带歌曲 id，可继续调用 get_lyric / get_simi_song 深入介绍某首歌。
    (注意：游客身份也可使用)
    """
    cookie = get_cookie_from_config(config)
    limit = min(max(limit, 1), 30)

    try:
        data = call_music_api("/recommend/songs", cookie)
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"

    if data.get("code") != 200:
        return f"获取每日推荐失败，API 接口返回状态码：{data.get('code')}"

    daily = (data.get("data") or {}).get("dailySongs") or []
    if not daily:
        return "暂无每日推荐数据。"

    # 语料沉淀：推荐结果落库并向量化（失败不影响本次回答）
    try:
        cache_songs(daily)
    except Exception as e:
        logger.warning("每日推荐沉淀失败: %s", e)

    shown = daily[:limit]
    footer = f"（推荐池共 {len(daily)} 首，仅展示前 {len(shown)} 首）" if len(daily) > len(shown) else ""
    lines = [f"{i}. {format_song(s)}" for i, s in enumerate(shown, 1)]
    return "每日推荐歌曲：\n" + "\n".join(lines) + footer


@tool
def get_recommend_playlists(limit: int = 8, config: RunnableConfig = None) -> str:
    """
    获取个性化推荐的歌单列表。
    当用户问"推荐几个歌单"、"有没有好听的歌单"时，请调用此工具。
    返回带歌单 id，可继续调用 get_playlist_detail 查看某个歌单的曲目。
    (注意：游客身份也可使用)
    """
    cookie = get_cookie_from_config(config)
    limit = min(max(limit, 1), 20)

    try:
        data = call_music_api("/personalized", cookie, {"limit": limit})
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"

    if data.get("code") != 200:
        return f"获取推荐歌单失败，API 接口返回状态码：{data.get('code')}"

    result = data.get("result") or []
    if not result:
        return "暂无推荐歌单数据。"

    lines = [
        f"{i}. 《{p.get('name', '未知')}》| {p.get('trackCount', '?')} 首"
        f"| 播放 {p.get('playCount', 0)} 次 | id={p.get('id')}"
        for i, p in enumerate(result, 1)
    ]
    return "为你推荐的歌单：\n" + "\n".join(lines)


@tool
def get_music_charts(config: RunnableConfig = None) -> str:
    """
    获取网易云音乐全部官方榜单列表（飙升榜、新歌榜、热歌榜、原创榜等，含更新频率）。
    当用户问"现在流行什么"、"有什么榜单"、"热歌榜里有什么"时，先调用此工具拿到榜单 id，
    再调用 get_playlist_detail(id) 查看榜单的具体曲目。
    (注意：游客身份也可使用)
    """
    cookie = get_cookie_from_config(config)

    try:
        data = call_music_api("/toplist", cookie)
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"

    if data.get("code") != 200:
        return f"获取榜单失败，API 接口返回状态码：{data.get('code')}"

    charts = data.get("list") or []
    if not charts:
        return "暂无榜单数据。"

    # 官方主榜排在最前，取前 20 个避免撑爆上下文
    shown = charts[:20]
    lines = [
        f"{i}. {c.get('name', '未知')}（{c.get('updateFrequency', '未知')}）| id={c.get('id')}"
        for i, c in enumerate(shown, 1)
    ]
    footer = f"（共 {len(charts)} 个榜单，仅展示前 {len(shown)} 个）" if len(charts) > len(shown) else ""
    return "网易云音乐官方榜单：\n" + "\n".join(lines) + footer
