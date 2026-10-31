"""行为类工具：听歌排行（数据经 store 缓存编排层）。"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from music_api import MusicAPIError, get_cookie_from_config
from store import get_uid, get_user_play_records as _store_play_records

_PERIOD_TYPE = {"week": 1, "all": 0}

_GUEST_HINT = (
    "获取失败：当前是游客身份，没有账号档案，无法查询用户数据。"
    "需要登录真实账号后才能使用此功能，可以引导用户稍后再试。"
)


@tool
def get_user_play_records(period: str = "week", config: RunnableConfig = None) -> str:
    """
    获取当前登录用户的听歌排行 Top 20（歌曲 | 歌手 | 播放次数）。
    当用户问"我最近在听什么"、"我的听歌排行"、"分析一下我的听歌风格"时，请调用此工具；
    拿到数据后可以基于歌手和流派分布为用户归纳听歌风格。
    period 参数：传 "week" 查最近一周，传 "all" 查所有时间。
    (注意：需要真实账号登录，游客身份无法查询)
    """
    cookie = get_cookie_from_config(config)
    period_key = period.strip().lower()
    if period_key not in _PERIOD_TYPE:
        return f"参数错误：period 只支持 'week'（最近一周）或 'all'（所有时间），收到的是 '{period}'。"

    try:
        uid = get_uid(cookie)
        if uid is None:
            return _GUEST_HINT
        result = _store_play_records(uid, _PERIOD_TYPE[period_key], cookie)
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"
    except Exception as e:
        return f"工具调用发生异常（数据层）：{e}"

    data = result["data"]
    records = data.get("records") or []

    raw = data.get("raw") or {}
    if not records and raw.get("code"):
        return f"获取听歌排行失败，API 接口返回状态码：{raw.get('code')}"
    if not records:
        return "查询成功，但该时间段内没有听歌记录。"

    lines = []
    for i, r in enumerate(records[:20], 1):
        song = r.get("song") or {}
        artists = "/".join(a.get("name", "?") for a in song.get("ar") or []) or "未知歌手"
        lines.append(f"{i}. 《{song.get('name', '未知')}》 - {artists} | 播放 {r.get('playCount', 0)} 次")

    label = "最近一周" if period_key == "week" else "所有时间"
    listen_total = data.get("listenSongs")
    total_part = f"，累计听歌 {listen_total} 首" if listen_total is not None else ""
    return f"查询成功（{label}{total_part}），听歌排行 Top {len(lines)}：\n" + "\n".join(lines)
