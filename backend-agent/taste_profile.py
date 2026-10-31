"""用户画像计算：听歌数据 → 结构化画像 + 画像文本 + 画像向量。

三要素：Top 歌手统计（规则聚合）、流派归纳与画像文本（LLM）、
画像向量（profile_text 经 bge 编码写入 Milvus user_taste）。
画像按 24h TTL 懒重算；游客（uid=None）静默跳过。
"""

import json
import logging
import time

from langchain_openai import ChatOpenAI

from config import get_settings
from db import (
    get_conn,
    get_feedback_filter,
    get_profile,
    get_recent_play_events,
    save_profile,
)
from embedder import embed_texts
from milvus_client import upsert_taste_vector
from store import get_user_play_records, sync_user_playlists

logger = logging.getLogger(__name__)

PROFILE_TTL = 24 * 3600


def ensure_profile(uid: int | None, cookie: str, force: bool = False) -> dict | None:
    """画像新鲜度检查与懒重算；游客返回 None。"""
    if not uid:
        return None
    if not force:
        with get_conn() as conn:
            cached = get_profile(conn, uid)
        if cached and (time.time() - cached["updated_at"].timestamp()) < PROFILE_TTL:
            return dict(cached)
    return compute_profile(uid, cookie)


def compute_profile(uid: int, cookie: str) -> dict:
    """采集听歌数据 → 聚合统计 → LLM 归纳 → 入库 + 向量化。

    聚合融合三类信号：听歌排行（次数）、播放行为 play_events（×5，主动意图）、
    赞过反馈（×10 等效播放）；踩过的歌曲从统计中剔除。
    """
    week = get_user_play_records(uid, 1, cookie)["data"]["records"]
    all_recs = get_user_play_records(uid, 0, cookie)["data"]["records"]
    sync_user_playlists(uid, cookie)  # 顺带沉淀本人歌单元数据

    # 稀疏数据保护：绕过 music-api 缓存后每次都是真实调用，网易偶发限流会返回
    # 空稀疏数据——若远少于既有画像的数据量，保留旧画像不覆盖（否则稀疏结果会被缓存 24h）
    with get_conn() as conn:
        prev = get_profile(conn, uid)
    if len(all_recs) < 10 and prev:
        logger.warning(
            "听歌数据异常稀疏（all=%d week=%d），保留既有画像不覆盖", len(all_recs), len(week)
        )
        return dict(prev)

    # 反馈与播放行为融合
    with get_conn() as conn:
        play_ev = get_recent_play_events(conn, uid, days=30)
        liked_ids, disliked_ids = get_feedback_filter(conn, uid, dislike_days=3650)
    pe_counts = {r["song_id"]: r["play_times"] for r in play_ev}

    # 全时段聚合与近期信号（play_events/liked）尺度相差悬殊（千级 vs 个位数），
    # 各自归一化后加权融合，避免反馈信号被绝对值淹没
    artists_ranked = _blend_artists(all_recs, liked_ids, disliked_ids, pe_counts)

    # 稀疏数据保护（后置）：记录数正常但歌手字段大面积为空的情况在这里兜底
    if len(artists_ranked) < 5 and prev:
        logger.warning("融合歌手数异常（%d 位），保留既有画像不覆盖", len(artists_ranked))
        return dict(prev)

    artists_all = artists_ranked[:20]
    artists_week = _top_artists(week, 10)
    total_plays = sum(r.get("playCount") or 0 for r in all_recs if (r.get("song") or {}).get("id") not in disliked_ids)

    genres, profile_text = _llm_profile_text(artists_all, artists_week, total_plays)

    row = {
        "netease_uid": uid,
        "favorite_genres": genres,
        "favorite_artists": [name for name, _ in artists_all[:15]],
        "era_dist": {},  # 年代分布需要专辑发行时间，后续补充
        "profile_text": profile_text,
    }
    with get_conn() as conn:
        save_profile(conn, row)

    upsert_taste_vector(uid, embed_texts([profile_text])[0])
    logger.info("画像已计算 uid=%s genres=%s", uid, list(genres)[:5])
    return row


def _top_artists(records: list[dict], n: int) -> list[tuple[str, int]]:
    """按播放次数聚合歌手。"""
    agg: dict[str, int] = {}
    for r in records:
        for a in (r.get("song") or {}).get("ar") or []:
            name = a.get("name")
            if name:
                agg[name] = agg.get(name, 0) + (r.get("playCount") or 0)
    return sorted(agg.items(), key=lambda kv: -kv[1])[:n]


def _blend_artists(
    records: list[dict],
    liked_ids: set[int],
    disliked_ids: set[int],
    pe_counts: dict[int, int],
) -> list[tuple[str, float]]:
    """歌手综合分：全时段播放（0.7）与近期信号（0.3）各自归一化后融合。

    近期信号 = play_events 次数 + liked 每歌 +10（主动意图强）。
    踩过的歌曲从两个维度都剔除。
    """
    alltime: dict[str, int] = {}
    signal: dict[str, float] = {}
    record_ids: set[int] = set()

    for r in records:
        song = r.get("song") or {}
        sid = song.get("id")
        if sid is None:
            continue
        record_ids.add(sid)
        if sid in disliked_ids:
            continue
        plays = r.get("playCount") or 0
        sig = 5 * pe_counts.get(sid, 0) + (10 if sid in liked_ids else 0)
        for a in song.get("ar") or []:
            name = a.get("name")
            if not name:
                continue
            alltime[name] = alltime.get(name, 0) + plays
            signal[name] = signal.get(name, 0) + sig

    # liked 歌曲不在榜单记录里的，其歌手直接注入近期信号（等效满分信号）
    missing = liked_ids - record_ids
    if missing:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT song_id, artists FROM songs WHERE song_id = ANY(%s)",
                (list(missing),),
            ).fetchall()
        for r in rows:
            if r["song_id"] in disliked_ids:
                continue
            for a in r["artists"] or []:
                signal[a] = signal.get(a, 0) + 10.0

    if not alltime and not signal:
        return []

    names = set(alltime) | set(signal)
    max_a = max(alltime.values(), default=0) or 1
    max_s = max(signal.values(), default=0) or 1
    scored = [
        (name, round(0.7 * alltime.get(name, 0) / max_a + 0.3 * signal.get(name, 0) / max_s, 3))
        for name in names
    ]
    return sorted(scored, key=lambda kv: (-kv[1], kv[0]))


def _llm_profile_text(
    artists_all: list[tuple[str, float]],
    artists_week: list[tuple[str, int]],
    total_plays: int,
) -> tuple[dict, str]:
    """LLM 归纳流派权重与画像文本；失败时降级为规则版。"""
    settings = get_settings()
    try:
        llm = ChatOpenAI(
            model=settings.llm_model,
            temperature=0.2,
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
        )
        artist_lines = "\n".join(f"- {name}（综合分 {score:.2f}）" for name, score in artists_all)
        week_names = "、".join(name for name, _ in artists_week) or "（无）"
        prompt = (
            "以下是一位网易云音乐用户的真实听歌数据，请归纳其音乐画像。\n\n"
            f"累计榜单播放 {total_plays} 次；最近一周常听歌手：{week_names}\n\n"
            f"累计 Top 歌手：\n{artist_lines}\n\n"
            "请只返回 JSON，不要任何其他文字：\n"
            '{"genres": {"流派或场景名": 权重0到1（最多5个）}, '
            '"profile_text": "用第三人称写 2~3 句画像，概括口味、场景与情绪偏好，'
            '供 AI 助手了解该用户，口语化"}'
        )
        resp = llm.invoke(prompt)
        content = resp.content.strip()
        if content.startswith("```"):
            content = content.strip("`").lstrip("json").strip()
        data = json.loads(content)
        genres = {str(k): round(float(v), 2) for k, v in (data.get("genres") or {}).items()}
        text = (data.get("profile_text") or "").strip()
        if text:
            return (genres or {"未分类": 1.0}), text
    except Exception as e:
        logger.warning("LLM 画像归纳失败，降级规则版: %s", e)

    names = "、".join(name for name, _ in artists_all[:10]) or "暂无数据"
    return {"未分类": 1.0}, f"常听歌手：{names}。累计榜单播放 {total_plays} 次。"
