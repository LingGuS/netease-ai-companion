"""music-api 统一客户端：所有工具的 HTTP 调用收口于此。

职责：超时与重试、错误归一化、统一日志、公共瘦身辅助。
各接口专属的数据瘦身逻辑留在对应工具内——每个接口要瘦的字段不同。
"""

import logging
import time

import requests
from langchain_core.runnables import RunnableConfig

from config import get_settings

logger = logging.getLogger(__name__)


class MusicAPIError(RuntimeError):
    """music-api 调用失败（连接失败、重试耗尽）。"""


def get_cookie_from_config(config: RunnableConfig) -> str:
    """从请求级配置中取当前会话的 cookie（由后端注入，不进 state、不落盘）。"""
    return (config.get("configurable") or {}).get("cookie", "")


def call_music_api(path: str, cookie: str, data: dict | None = None, retries: int = 2) -> dict:
    """调用 music-api 的指定接口，成功返回解析后的 JSON dict。

    cookie 以表单字段传递；网络异常、5xx 与响应解析失败做指数退避重试，
    重试耗尽抛 MusicAPIError。业务层面的 code != 200 由各工具自行处理
    （music-api 对网易云业务错误通常仍返回可解析的 JSON）。
    """
    url = f"{get_settings().music_api_base_url}{path}"
    # music-api 有全局 2 分钟响应缓存中间件，且缓存键只含 URL 不含 POST body——
    # 不同参数的同路径请求会拿到同一份缓存（实测踩坑）。附加时间戳参数绕过。
    url = f"{url}{'&' if '?' in url else '?'}_t={int(time.time() * 1000)}"
    payload = {"cookie": cookie, **(data or {})}

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        start = time.perf_counter()
        try:
            response = requests.post(url, data=payload, timeout=15)
            elapsed = time.perf_counter() - start
            if response.status_code >= 500:
                last_error = MusicAPIError(f"HTTP {response.status_code}")
                logger.warning("music-api %s 第 %d 次返回 %d", path, attempt + 1, response.status_code)
            else:
                result = response.json()
                logger.info("music-api %s -> code=%s (%.2fs)", path, result.get("code"), elapsed)
                return result
        except (requests.exceptions.RequestException, ValueError) as e:
            last_error = e
            logger.warning("music-api %s 第 %d 次调用异常: %s", path, attempt + 1, e)

        if attempt < retries:
            time.sleep(0.5 * (2**attempt))  # 0.5s / 1s 指数退避

    raise MusicAPIError(f"调用 {path} 失败（已重试 {retries} 次）：{last_error}")


def format_song(song: dict) -> str:
    """歌曲对象统一瘦身：歌名 | 歌手 | 专辑 | 时长 | id。

    歌曲类接口（搜索/推荐/歌单曲目/详情）的字段结构一致：
    name, id, ar[{'name'}], al{'name'}, dt(毫秒), pop(0-100)。
    """
    artists = "/".join(a.get("name", "?") for a in song.get("ar") or []) or "未知歌手"
    album = (song.get("al") or {}).get("name") or "未知专辑"
    total_sec = round((song.get("dt") or 0) / 1000)
    duration = f"{total_sec // 60}:{total_sec % 60:02d}"
    return f"《{song.get('name', '未知')}》 - {artists} | 专辑：{album} | {duration} | id={song.get('id')}"
