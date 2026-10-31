"""歌单类工具：歌单详情与用户歌单列表（数据经 store 缓存编排层）。"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from music_api import MusicAPIError, get_cookie_from_config
from store import get_playlist_detail as _store_playlist_detail
from store import get_uid, sync_user_playlists

# 游客身份提示：涉及"我的"数据的工具统一走这句话
_GUEST_HINT = (
    "获取失败：当前是游客身份，没有账号档案，无法查询用户数据。"
    "需要登录真实账号后才能使用此功能，可以引导用户稍后再试。"
)


@tool
def get_playlist_detail(playlist_id: int, limit: int = 20, config: RunnableConfig = None) -> str:
    """
    获取指定歌单（或官方榜单）的详细信息：歌单名、创建者、曲目总数、播放量和曲目列表。
    当用户想知道"这个歌单里有什么歌"、"热歌榜/飙升榜里有哪些歌曲"时，请调用此工具。
    playlist_id 可从搜索结果、推荐歌单或榜单工具的返回中获得。
    (注意：曲目列表默认最多返回前 20 首，可通过 limit 调整，最大 50)
    """
    cookie = get_cookie_from_config(config)
    limit = min(max(limit, 1), 50)

    try:
        result = _store_playlist_detail(playlist_id, cookie)
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"
    except Exception as e:
        return f"工具调用发生异常（数据层）：{e}"

    data = result["data"]
    if data.get("code") != 200:
        return f"获取歌单详情失败，API 接口返回状态码：{data.get('code')}"

    playlist = data.get("playlist") or {}
    if not playlist:
        return "获取失败：没有找到该歌单，请确认歌单 id 是否正确。"

    name = playlist.get("name", "未知歌单")
    creator = (playlist.get("creator") or {}).get("nickname", "未知")
    track_count = playlist.get("trackCount", 0)
    play_count = playlist.get("playCount", 0)
    description = (playlist.get("description") or "").strip()[:100]

    header = (
        f"查询成功。歌单《{name}》：\n"
        f"- 创建者：{creator}\n"
        f"- 曲目总数：{track_count} 首\n"
        f"- 总播放量：{play_count}"
    )
    if description:
        header += f"\n- 简介：{description}"

    tracks = (playlist.get("tracks") or [])[:limit]
    if not tracks:
        return header + "\n- 曲目列表为空"

    from music_api import format_song

    listing = "\n".join(f"{i}. {format_song(s)}" for i, s in enumerate(tracks, 1))
    footer = f"\n（共 {track_count} 首，仅展示前 {len(tracks)} 首）" if track_count > len(tracks) else ""
    return header + "\n曲目列表：\n" + listing + footer


@tool
def get_user_playlists(config: RunnableConfig = None) -> str:
    """
    获取当前登录用户的所有歌单列表（自己创建的 + 收藏的）。
    当用户询问"我的歌单"、"我创建了哪些歌单"、"看看我的歌单"时，请调用此工具。
    拿到歌单 id 后，可再调用 get_playlist_detail 查看某个歌单的曲目。
    (注意：需要真实账号登录，游客身份无法查询)
    """
    cookie = get_cookie_from_config(config)

    try:
        uid = get_uid(cookie)
        if uid is None:
            return _GUEST_HINT
        raw = sync_user_playlists(uid, cookie)
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"
    except Exception as e:
        return f"工具调用发生异常（数据层）：{e}"

    if raw.get("code") != 200:
        return f"获取用户歌单失败，API 接口返回状态码：{raw.get('code')}"

    playlists = raw.get("playlist") or []
    if not playlists:
        return "该用户还没有任何歌单。"

    lines = []
    for i, p in enumerate(playlists, 1):
        tag = "创建" if p.get("creator", {}).get("userId") == uid else "收藏"
        lines.append(
            f"{i}. 《{p.get('name', '未知')}》（{tag}）| {p.get('trackCount', 0)} 首"
            f"| 播放 {p.get('playCount', 0)} 次 | id={p.get('id')}"
        )
    return f"查询成功，共 {len(playlists)} 个歌单：\n" + "\n".join(lines)
