"""播放类工具：获取播放 URL 并产出前端音乐卡片。

卡片协议约定：工具返回 JSON 串中的 "_card" 键是前端卡片载荷，
main.py 会从本轮 ToolMessage 中抽取并挂到 ChatResponse.cards。
卡片字段一律来自工具链（接口/数据库），禁止 LLM 编造。
"""

import json

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from music_api import MusicAPIError, call_music_api, get_cookie_from_config
from store import get_songs_by_ids


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
        # 1. 歌曲元数据（PG 优先，缺则调接口落库）——卡片字段来自工具链
        meta_res = get_songs_by_ids([song_id], cookie)
        songs = meta_res["data"]["songs"]
        song = songs[0] if songs else None

        # 2. 播放 URL（实时取，20 分钟时效签名链接，不落库）
        #    文档要求 cookie 携带 os=pc 以保证正常码率
        data = call_music_api(
            "/song/url/v1",
            cookie,
            {"id": song_id, "level": level, "cookie": f"{cookie}; os=pc".strip("; ")},
        )
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"
    except Exception as e:
        return f"工具调用发生异常（数据层）：{e}"

    url_item = (data.get("data") or [{}])[0]
    play_url = url_item.get("url")
    trial_info = url_item.get("freeTrialInfo")

    payload = {
        "song_id": song_id,
        "name": (song or {}).get("name") or f"歌曲 {song_id}",
        "artists": [a.get("name") for a in (song or {}).get("ar") or []],
        "album": ((song or {}).get("al") or {}).get("name"),
        "cover_url": ((song or {}).get("al") or {}).get("picUrl"),
        "play_url": play_url,
        "playable": bool(play_url),
        "trial_info": trial_info,
        "level": level,
    }
    if not payload["playable"]:
        payload["message"] = "当前登录身份无法播放该歌曲，登录真实账号后可播放。"

    card = {"type": "music", "payload": payload}
    return json.dumps({"_card": card, "result": "播放卡片已生成，请告知用户已为其准备播放。" if payload["playable"] else "无法获取播放链接。"}, ensure_ascii=False)
