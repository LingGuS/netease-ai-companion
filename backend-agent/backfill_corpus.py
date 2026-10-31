"""语料池扩充：拉取官方榜单曲目入向量库，扩大语义检索可用范围。

逐个榜单走 store.get_playlist_detail 现有同步管道（自动落库 + 向量化），
幂等可重跑（重复歌曲 upsert 跳过）。

注意：Milvus Lite 的 .db 文件同一时刻只能被一个进程打开，
运行前请先停止 FastAPI 后端。

用法:
    cd backend-agent
    python backfill_corpus.py
"""

import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("backfill-corpus")

# 官方榜单名称白名单（匹配不到的自动跳过）
WANTED_CHARTS = [
    "飙升榜",
    "新歌榜",
    "原创榜",
    "热歌榜",
    "网易云ACG榜",
    "网易云电音榜",
    "网易云古典榜",
    "网易云中文说唱榜",
]

# 场景歌单关键词：每类取一个真实场景歌单同步入池（对场景检索的价值高于榜单）
MOOD_KEYWORDS = ["学习 工作专注", "雨天 安静", "运动 健身", "睡前 助眠", "咖啡馆 轻音乐"]


def main():
    from login import get_current_user
    from milvus_client import SONGS_COLLECTION, ensure_collections, get_milvus_client
    from music_api import call_music_api, MusicAPIError
    from store import get_playlist_detail

    ensure_collections()
    client = get_milvus_client()
    before = client.get_collection_stats(SONGS_COLLECTION)["row_count"]
    logger.info("扩充前曲库规模: %d 首", before)

    # cookie：当前登录用户优先，游客兜底（公开榜单游客也能拉）
    user = get_current_user()
    cookie = (user or {}).get("cookie") or ""
    logger.info("同步身份: %s", user["nickname"] if user else "游客")

    charts = call_music_api("/toplist", cookie).get("list") or []
    by_name = {c.get("name"): c.get("id") for c in charts}

    selected = []
    for name in WANTED_CHARTS:
        chart_id = by_name.get(name)
        if chart_id:
            selected.append((name, chart_id))
        else:
            logger.warning("榜单「%s」未在 /toplist 返回中找到，跳过", name)

    start = time.perf_counter()
    for i, (name, chart_id) in enumerate(selected, 1):
        try:
            result = get_playlist_detail(chart_id, cookie)
            playlist = result["data"].get("playlist") or {}
            logger.info(
                "[%d/%d] %s(id=%s) 同步完成：%s 首",
                i, len(selected), name, chart_id, playlist.get("trackCount", "?"),
            )
        except MusicAPIError as e:
            logger.error("[%d/%d] %s 同步失败（跳过，可重跑）: %s", i, len(selected), name, e)

    # 场景歌单补充：按关键词搜歌单，各取一个曲目数适中（30~300 首）的同步入池
    for kw in MOOD_KEYWORDS:
        try:
            result = call_music_api(
                "/cloudsearch", cookie, {"keywords": kw, "type": 1000, "limit": 5}
            )
            candidates = (result.get("result") or {}).get("playlists") or []
            picked = next(
                (p for p in candidates if 30 <= (p.get("trackCount") or 0) <= 300), None
            )
            if not picked:
                logger.warning("场景「%s」未找到合适歌单，跳过", kw)
                continue
            result = get_playlist_detail(picked["id"], cookie)
            playlist = result["data"].get("playlist") or {}
            logger.info(
                "场景歌单「%s」(关键词=%s) 同步完成：%s 首",
                playlist.get("name", "?"), kw, playlist.get("trackCount", "?"),
            )
        except MusicAPIError as e:
            logger.error("场景「%s」同步失败（跳过）: %s", kw, e)

    after = client.get_collection_stats(SONGS_COLLECTION)["row_count"]
    logger.info(
        "语料扩充完成：%d 首 → %d 首（净增 %d），耗时 %.1fs",
        before, after, after - before, time.perf_counter() - start,
    )


if __name__ == "__main__":
    main()
