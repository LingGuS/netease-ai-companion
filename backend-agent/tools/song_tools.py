"""歌曲类工具：批量详情、歌词、相似歌曲（详情经 store 缓存编排层）。"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from music_api import MusicAPIError, call_music_api, format_song, get_cookie_from_config
from store import get_songs_by_ids


@tool
def get_song_detail(song_ids: str, config: RunnableConfig = None) -> str:
    """
    批量获取歌曲的详细信息（歌名 | 歌手 | 专辑 | 时长 | 热度）。
    当你需要补充某几首歌的详细信息（如热度、专辑、时长）时调用此工具。
    song_ids 为逗号分隔的歌曲 id，如 "186016,347230"，id 可从搜索或推荐结果中获得。
    (注意：单次最多查询 20 首)
    """
    cookie = get_cookie_from_config(config)
    ids = [s.strip() for s in song_ids.split(",") if s.strip().isdigit()]
    if not ids:
        return f"参数错误：song_ids 需要是逗号分隔的歌曲数字 id，收到的是 '{song_ids}'。"
    ids = [int(i) for i in ids][:20]

    try:
        result = get_songs_by_ids(ids, cookie)
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"
    except Exception as e:
        return f"工具调用发生异常（数据层）：{e}"

    songs = result["data"]["songs"]
    if not songs:
        return "没有查询到这些歌曲的详情，请确认 id 是否正确。"

    lines = [f"{i}. {format_song(s)} | 热度 {s.get('pop', '?')}/100" for i, s in enumerate(songs, 1)]
    return f"查询成功，共 {len(songs)} 首歌曲详情：\n" + "\n".join(lines)


@tool
def get_lyric(song_id: int, config: RunnableConfig = None) -> str:
    """
    获取指定歌曲的歌词文本。
    当用户问"这首歌的歌词是什么"、"这首歌讲了什么"时，请调用此工具。
    song_id 从搜索或推荐结果中获得。
    """
    cookie = get_cookie_from_config(config)

    try:
        data = call_music_api("/lyric", cookie, {"id": song_id})
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"

    if data.get("nolyric"):
        return "该歌曲为纯音乐，没有歌词。"

    lrc = (data.get("lrc") or {}).get("lyric") or ""
    if not lrc:
        return "没有获取到歌词。"

    lines = [line for line in lrc.splitlines() if line.strip()]
    if len(lines) > 80:
        body = "\n".join(lines[:80]) + f"\n（歌词较长，仅展示前 80 行，共 {len(lines)} 行）"
    else:
        body = "\n".join(lines)
    return f"歌词（id={song_id}）：\n{body}"


@tool
def get_simi_song(song_id: int, config: RunnableConfig = None) -> str:
    """
    获取与指定歌曲相似的歌曲列表，用于"找和这首歌像的歌"。
    song_id 从搜索或推荐结果中获得。
    (注意：游客身份下该接口可能没有数据；返回为空时请如实告知，
    并改用 get_daily_recommend_songs 或 search_music 来满足用户)
    """
    cookie = get_cookie_from_config(config)

    try:
        data = call_music_api("/simi/song", cookie, {"id": song_id})
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"

    if data.get("code") != 200:
        return f"获取相似歌曲失败，API 接口返回状态码：{data.get('code')}"

    songs = data.get("songs") or []
    if not songs:
        return (
            "暂无相似歌曲数据（游客身份下常见）。"
            "可以改用 get_daily_recommend_songs 或 search_music 来找类似的歌曲。"
        )

    shown = songs[:15]
    footer = f"（共 {len(songs)} 首，仅展示前 {len(shown)} 首）" if len(songs) > len(shown) else ""
    lines = [f"{i}. {format_song(s)}" for i, s in enumerate(shown, 1)]
    return "相似歌曲：\n" + "\n".join(lines) + footer
