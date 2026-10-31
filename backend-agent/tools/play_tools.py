"""播放类工具：获取播放 URL 并产出前端音乐卡片。

卡片协议约定：工具返回 JSON 串中的 "_card" 键是前端卡片载荷，
main.py 会从本轮 ToolMessage 中抽取并挂到 ChatResponse.cards。
卡片字段一律来自工具链（接口/数据库），禁止 LLM 编造。

get_play_payload 同时被 POST /play/url 复用：
前端「上一首/下一首/列表点播」不经过 LLM 直取播放链接。
"""

import json

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from music_api import MusicAPIError, call_music_api, get_cookie_from_config
from store import get_songs_by_ids


def get_play_payload(song_id: int, level: str, cookie: str) -> dict:
    """组装播放载荷：歌曲元数据（PG 优先，缺则调接口落库）+ 实时播放 URL。

    播放 URL 为 20 分钟时效签名链接（与音质/权益绑定），永不落库。
    游客身份拿不到任何播放 URL，此时 payload.playable=False 并携带降级提示。
    抛出 MusicAPIError 由调用方处理。
    """
    # 1. 歌曲元数据——卡片字段来自工具链
    meta_res = get_songs_by_ids([song_id], cookie)
    songs = meta_res["data"]["songs"]
    song = songs[0] if songs else None

    # 2. 播放 URL（实时取）；文档要求 cookie 携带 os=pc 以保证正常码率
    data = call_music_api(
        "/song/url/v1",
        cookie,
        {"id": song_id, "level": level, "cookie": f"{cookie}; os=pc".strip("; ")},
    )

    url_item = (data.get("data") or [{}])[0]
    play_url = url_item.get("url")
    trial_info = url_item.get("freeTrialInfo")

    payload = {
        "song_id": song_id,
        "name": (song or {}).get("name") or f"歌曲 {song_id}",
        "artists": [a.get("name") for a in (song or {}).get("ar") or []],
        "album": ((song or {}).get("al") or {}).get("name"),
        "cover_url": ((song or {}).get("al") or {}).get("picUrl"),
        "duration_ms": (song or {}).get("dt") or ((song or {}).get("duration") or 0),
        "play_url": play_url,
        "playable": bool(play_url),
        "trial_info": trial_info,
        "level": level,
    }
    if not payload["playable"]:
        payload["message"] = "当前登录身份无法播放该歌曲，登录真实账号后可播放。"
    return payload


_CONTROL_ACTIONS = ("play", "pause", "next", "prev", "stop")

_CONTROL_RESULT = {
    "play": "已请求前端继续播放。",
    "pause": "已请求前端暂停播放。",
    "next": "已请求前端切换到下一首。",
    "prev": "已请求前端切换到上一首。",
    "stop": "已请求前端停止播放。",
}


@tool
def control_player(action: str, config: RunnableConfig = None) -> str:
    """
    控制前端播放器：play 继续播放 / pause 暂停 / next 下一首 / prev 上一首 / stop 停止。
    当用户下达播放控制指令（"暂停一下"、"继续放"、"下一首"、"上一首"、"别放了"）时调用。
    注意：控制的是前端正在播放的歌曲，只有本轮对话此前确实播放过歌曲时才有意义，
    没有在播的歌时请直接告知用户，不要调用本工具。action 必须是 play/pause/next/prev/stop 之一。
    """
    action = (action or "").strip().lower()
    if action not in _CONTROL_ACTIONS:
        return f"未知的播放控制指令：{action}（只能是 play/pause/next/prev/stop）"
    card = {"type": "player_control", "payload": {"action": action}}
    return json.dumps({"_card": card, "result": _CONTROL_RESULT[action]}, ensure_ascii=False)


@tool
def play_song(song_id: int, level: str = "exhigh", config: RunnableConfig = None) -> str:
    """
    播放指定歌曲：获取播放链接并让前端弹出音乐播放卡片。
    当用户说"播放这首歌"、"来听《xx》"、"放一下这首"时，请调用此工具。
    song_id 从搜索或推荐结果中获得；level 为音质（standard/exhigh/lossless 等，默认 exhigh）。
    注意：游客身份可能无法播放（返回 playable=false），此时如实告知用户"登录后可播放"。
    """
    cookie = get_cookie_from_config(config)

    try:
        payload = get_play_payload(song_id, level, cookie)
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"
    except Exception as e:
        return f"工具调用发生异常（数据层）：{e}"

    card = {"type": "music", "payload": payload}
    return json.dumps({"_card": card, "result": "播放卡片已生成，请告知用户已为其准备播放。" if payload["playable"] else "无法获取播放链接。"}, ensure_ascii=False)
