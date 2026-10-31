"""混合推荐引擎：多源召回 → 过滤 → 加权排序。

召回四路（各源独立容错）：口味画像向量 / 场景向量（可选）/ 官方每日推荐 /
播放延伸（近期主动播放歌曲的相似歌）。过滤掉最近播放过、30 天内踩过、
已赞过的；排序 = max(来源分) + 多源命中加分 + liked 歌手加分。
"""

import logging

from db import get_conn, get_feedback_filter, get_recent_play_events
from embedder import embed_query
from login import get_current_user
from milvus_client import get_taste_vector, search_songs
from music_api import call_music_api

logger = logging.getLogger(__name__)

_MULTI_SOURCE_BONUS = 0.15   # 每多命中一个召回源 +0.15
_LIKED_ARTIST_BONUS = 0.2    # 候选歌手在 liked 列表中 +0.2
_RECALL_SIZE = 30            # 每路召回条数
_EXTEND_SEEDS = 3            # 播放延伸取最近 3 首主动播放
_RECALL_SOURCE = {"口味画像", "场景匹配"}  # 向量类来源（分值即相似度）


def _api_song_to_hit(s: dict) -> dict | None:
    """把网易云歌曲对象转成与 Milvus 检索结果同构的 hit，便于统一合并。"""
    if not s.get("id"):
        return None
    return {
        "id": s["id"],
        "entity": {
            "name": s.get("name"),
            "artists": "/".join(a.get("name", "") for a in s.get("ar") or []),
            "album": (s.get("al") or {}).get("name"),
        },
    }


def _liked_artists(liked_ids: set[int]) -> set[str]:
    """liked 歌曲的歌手集合（加分用）。"""
    if not liked_ids:
        return set()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT artists FROM songs WHERE song_id = ANY(%s)",
            (list(liked_ids)[:50],),
        ).fetchall()
    artists: set[str] = set()
    for r in rows:
        artists.update(r["artists"] or [])
    return artists


def recommend_hybrid(cookie: str, limit: int = 10, scene_query: str | None = None) -> dict:
    """返回 {"candidates": [...], "error": None}；游客返回 {"error": "guest"}。"""
    user = get_current_user()
    if not user:
        return {"error": "guest"}
    uid = user["netease_uid"]

    with get_conn() as conn:
        liked_ids, disliked_ids = get_feedback_filter(conn, uid)
        recent = get_recent_play_events(conn, uid, days=7)
    recent_played = {r["song_id"] for r in recent}
    extend_seeds = [r["song_id"] for r in recent[:_EXTEND_SEEDS]]

    # ---- ① 多源召回 ----
    candidates: dict[int, dict] = {}

    def _add_source(hits: list[dict], source: str, base_score: float | None = None):
        for h in hits:
            sid = h.get("id")
            entity = h.get("entity") or {}
            if not sid or not entity.get("name"):
                continue
            c = candidates.setdefault(
                sid,
                {
                    "song_id": sid,
                    "name": entity.get("name"),
                    "artists": entity.get("artists") or "未知歌手",
                    "album": entity.get("album") or "未知专辑",
                    "scores": {},
                    "sources": [],
                },
            )
            score = h.get("distance", base_score or 0.0)
            c["scores"][source] = max(score, c["scores"].get(source, 0.0))
            if source not in c["sources"]:
                c["sources"].append(source)

    # A 画像近邻
    try:
        vec = get_taste_vector(uid)
        if vec:
            _add_source(search_songs(vec, _RECALL_SIZE), "口味画像")
    except Exception as e:
        logger.warning("画像召回失败（跳过该源）: %s", e)

    # B 场景近邻（可选）
    if scene_query:
        try:
            _add_source(search_songs(embed_query(scene_query), _RECALL_SIZE), "场景匹配")
        except Exception as e:
            logger.warning("场景召回失败（跳过该源）: %s", e)

    # C 官方每日推荐
    try:
        daily = (call_music_api("/recommend/songs", cookie).get("data") or {}).get("dailySongs") or []
        _add_source(
            [h for h in map(_api_song_to_hit, daily) if h][:20],
            "官方每日推荐", 0.5,
        )
    except Exception as e:
        logger.warning("官方推荐召回失败（跳过该源）: %s", e)

    # D 播放延伸：近期主动播放歌曲的相似歌
    for seed in extend_seeds:
        try:
            simi = call_music_api("/simi/song", cookie, {"id": seed}).get("songs") or []
            _add_source(
                [h for h in map(_api_song_to_hit, simi) if h][:10],
                "播放延伸", 0.6,
            )
        except Exception as e:
            logger.warning("播放延伸召回失败 seed=%s: %s", seed, e)

    # ---- ② 过滤 ----
    for sid in list(candidates):
        if sid in disliked_ids or sid in recent_played or sid in liked_ids:
            del candidates[sid]

    # ---- ③ 加权排序 ----
    liked_artists = _liked_artists(liked_ids)
    ranked = []
    for c in candidates.values():
        score = max(c["scores"].values())
        score += _MULTI_SOURCE_BONUS * (len(c["sources"]) - 1)
        if set(c["artists"].split("/")) & liked_artists:
            score += _LIKED_ARTIST_BONUS
        c["score"] = round(score, 3)
        ranked.append(c)
    ranked.sort(key=lambda c: (-c["score"], c["song_id"]))  # 次序键保证同分稳定

    return {"candidates": ranked[:limit], "error": None}
