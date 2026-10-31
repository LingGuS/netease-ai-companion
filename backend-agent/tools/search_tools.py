"""搜索类工具：关键词搜索歌曲/歌手/专辑/歌单（结果歌曲经 store 落库沉淀）。"""

import logging

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from music_api import MusicAPIError, call_music_api, format_song, get_cookie_from_config
from store import cache_songs

logger = logging.getLogger(__name__)

_SEARCH_TYPE = {"song": 1, "album": 10, "artist": 100, "playlist": 1000}


@tool
def search_music(keyword: str, search_type: str = "song", limit: int = 10, config: RunnableConfig = None) -> str:
    """
    按关键词搜索网易云音乐的内容，search_type 支持四种：
    - "song"：歌曲（默认）
    - "artist"：歌手
    - "album"：专辑
    - "playlist"：歌单
    当用户想找某首歌/某位歌手/某张专辑/某个歌单，或提到具体歌曲名时，请调用此工具。
    返回结果带 id：歌曲 id 可继续调用 get_song_detail / get_lyric / get_simi_song，
    歌单 id 可调用 get_playlist_detail 查看曲目。
    """
    cookie = get_cookie_from_config(config)
    type_key = search_type.strip().lower()
    if type_key not in _SEARCH_TYPE:
        return f"参数错误：search_type 只支持 song/artist/album/playlist，收到的是 '{search_type}'。"

    limit = min(max(limit, 1), 30)

    try:
        data = call_music_api(
            "/cloudsearch",
            cookie,
            {"keywords": keyword, "type": _SEARCH_TYPE[type_key], "limit": limit},
        )
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"

    if data.get("code") != 200:
        return f"搜索失败，API 接口返回状态码：{data.get('code')}"

    result = data.get("result") or {}

    if type_key == "song":
        songs = result.get("songs") or []
        if not songs:
            return f"没有搜索到与「{keyword}」相关的歌曲。"
        # 语料沉淀：搜索结果落库并向量化（失败不影响本次回答）
        try:
            cache_songs(songs)
        except Exception as e:
            logger.warning("搜索结果沉淀失败: %s", e)
        total = result.get("songCount", len(songs))
        lines = [f"{i}. {format_song(s)}" for i, s in enumerate(songs, 1)]
        footer = f"（共 {total} 条结果）" if total > len(songs) else ""
        return f"搜索「{keyword}」成功，歌曲结果：\n" + "\n".join(lines) + footer

    if type_key == "artist":
        artists = result.get("artists") or []
        if not artists:
            return f"没有搜索到与「{keyword}」相关的歌手。"
        lines = [
            f"{i}. {a.get('name', '未知')} | id={a.get('id')} | 歌曲 {a.get('musicSize', '?')} 首"
            for i, a in enumerate(artists, 1)
        ]
        return f"搜索「{keyword}」成功，歌手结果：\n" + "\n".join(lines)

    if type_key == "album":
        albums = result.get("albums") or []
        if not albums:
            return f"没有搜索到与「{keyword}」相关的专辑。"
        lines = [
            f"{i}. 《{a.get('name', '未知')}》 - {(a.get('artist') or {}).get('name', '未知歌手')}"
            f" | {a.get('size', '?')} 首 | id={a.get('id')}"
            for i, a in enumerate(albums, 1)
        ]
        return f"搜索「{keyword}」成功，专辑结果：\n" + "\n".join(lines)

    # playlist
    playlists = result.get("playlists") or []
    if not playlists:
        return f"没有搜索到与「{keyword}」相关的歌单。"
    lines = [
        f"{i}. 《{p.get('name', '未知')}》| {p.get('trackCount', '?')} 首"
        f"| 播放 {p.get('playCount', 0)} 次 | id={p.get('id')}"
        for i, p in enumerate(playlists, 1)
    ]
    return f"搜索「{keyword}」成功，歌单结果：\n" + "\n".join(lines)
