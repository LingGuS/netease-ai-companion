"""数据编排层（cache-aside）：先查 PG → 过期/未命中调网易云 API → 落库 → 向量化。

铁律：PG 是事实源，Milvus 是可重建的派生索引。写序先 PG 后 Milvus；
Milvus 失败不影响 PG 数据，embedded=false 的行由后续任意一次
sync_pending_embeddings() 或 rebuild_milvus.py 补偿。

对外返回结构化 dict（含 from_cache 元信息），LLM 文案由工具层拼装。
"""

import logging

from config import get_settings
from db import (
    get_conn,
    get_playlist_fresh,
    get_play_records_fresh,
    replace_playlist_tracks,
    upsert_play_records,
    upsert_playlists,
    upsert_songs,
)
from embedder import build_song_embed_text, embed_texts
from milvus_client import upsert_song_vectors
from music_api import call_music_api

logger = logging.getLogger(__name__)

TTL_PLAYLIST = 24 * 3600  # 歌单缓存 24h
TTL_PLAY_RECORDS = 24 * 3600  # 听歌排行缓存 24h


# ---------------------------------------------------------------------------
# 歌曲字段映射与向量化补偿
# ---------------------------------------------------------------------------

def song_raw_to_row(raw: dict) -> dict:
    """网易云歌曲裸字段（ar/al/dt/pop/fee）→ songs 表行（含 embed_text）。

    各歌曲类接口字段结构一致，此函数全局复用。
    """
    artists = [a.get("name", "") for a in raw.get("ar") or []]
    artist_ids = [a.get("id") for a in raw.get("ar") or [] if a.get("id")]
    album = raw.get("al") or {}
    name = raw.get("name") or "未知"
    album_name = album.get("name")
    return {
        "song_id": raw.get("id"),
        "name": name,
        "artists": artists,
        "artist_ids": artist_ids,
        "album_name": album_name,
        "album_id": album.get("id"),
        "duration_ms": raw.get("dt"),
        "pop": raw.get("pop"),
        "fee": raw.get("fee"),
        "embed_text": build_song_embed_text(name, artists, album_name),
        "cover_url": album.get("picUrl"),
    }


def sync_pending_embeddings() -> int:
    """把 PG 中 embedded=false 的歌曲向量化写入 Milvus，返回处理条数。"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT song_id, name, artists, album_name, pop, fee
            FROM songs WHERE embedded = false ORDER BY song_id
            """
        ).fetchall()
    if not rows:
        return 0

    texts = [build_song_embed_text(r["name"], r["artists"], r["album_name"]) for r in rows]
    vectors = embed_texts(texts)
    payload = [
        {
            "id": r["song_id"],
            "vector": vec,
            "name": r["name"],
            "artists": "/".join(r["artists"]),
            "album": r["album_name"] or "",
            "fee": r["fee"] or 0,
            "pop": r["pop"] or 0,
        }
        for r, vec in zip(rows, vectors)
    ]
    written = upsert_song_vectors(payload)

    with get_conn() as conn:
        conn.execute(
            "UPDATE songs SET embedded = true WHERE song_id = ANY(%s)",
            ([r["song_id"] for r in rows],),
        )
    logger.info("向量化入库 {} 首歌曲".format(written))
    return written


def cache_songs(raw_songs: list[dict]) -> None:
    """搜索/推荐结果的歌曲落库沉淀（不缓存响应本身）。"""
    rows = [song_raw_to_row(s) for s in raw_songs or [] if s.get("id")]
    if not rows:
        return
    with get_conn() as conn:
        upsert_songs(conn, rows)
    sync_pending_embeddings()


def get_uid(cookie: str) -> int | None:
    """取当前登录用户 uid；游客返回 None（由工具层输出降级文案）。"""
    data = call_music_api("/user/account", cookie)
    profile = (data.get("profile") or {}) if data.get("code") == 200 else {}
    return profile.get("userId")


# ---------------------------------------------------------------------------
# 歌单
# ---------------------------------------------------------------------------

def _persist_playlist(playlist: dict) -> None:
    """歌单详情落库：歌单元数据 + 曲目歌曲 + 关联关系。"""
    meta = {
        "playlist_id": playlist.get("id"),
        "name": playlist.get("name"),
        "creator_nickname": (playlist.get("creator") or {}).get("nickname"),
        "track_count": playlist.get("trackCount"),
        "play_count": playlist.get("playCount"),
        "description": (playlist.get("description") or "").strip() or None,
        "netease_uid": (playlist.get("creator") or {}).get("userId"),
    }
    tracks = playlist.get("tracks") or []
    song_rows = [song_raw_to_row(t) for t in tracks if t.get("id")]

    with get_conn() as conn:
        upsert_playlists(conn, [meta])
        upsert_songs(conn, song_rows)
        replace_playlist_tracks(conn, meta["playlist_id"], [r["song_id"] for r in song_rows])


def get_playlist_detail(playlist_id: int, cookie: str) -> dict:
    """歌单详情（cache-aside）。返回 {"data": <raw-api 形状>, "from_cache": bool}。"""
    with get_conn() as conn:
        cached = get_playlist_fresh(conn, playlist_id, TTL_PLAYLIST)
    if cached:
        tracks = [
            {
                "id": t["song_id"],
                "name": t["name"],
                "ar": [{"name": a} for a in (t["artists"] or [])],
                "al": {"name": t["album_name"]},
                "dt": t["duration_ms"],
            }
            for t in cached["tracks"]
        ]
        data = {
            "code": 200,
            "playlist": {
                "id": cached["playlist_id"],
                "name": cached["name"],
                "creator": {"nickname": cached["creator_nickname"]},
                "trackCount": cached["track_count"],
                "playCount": cached["play_count"],
                "description": cached["description"],
                "tracks": tracks,
            },
        }
        logger.info("歌单 %s 命中缓存（曲目 %d 首）", playlist_id, len(tracks))
        return {"data": data, "from_cache": True}

    raw = call_music_api("/playlist/detail", cookie, {"id": playlist_id})
    if raw.get("code") == 200 and raw.get("playlist"):
        _persist_playlist(raw["playlist"])
        sync_pending_embeddings()
    return {"data": raw, "from_cache": False}


def sync_user_playlists(uid: int, cookie: str) -> dict:
    """拉取并沉淀用户歌单列表（轻接口，透传不缓存；创建的歌单归属 uid）。"""
    raw = call_music_api("/user/playlist", cookie, {"uid": uid, "limit": 30})
    if raw.get("code") != 200:
        return raw

    metas = []
    for p in raw.get("playlist") or []:
        metas.append(
            {
                "playlist_id": p.get("id"),
                "name": p.get("name"),
                "creator_nickname": (p.get("creator") or {}).get("nickname"),
                "track_count": p.get("trackCount"),
                "play_count": p.get("playCount"),
                "description": (p.get("description") or "").strip() or None,
                # 只给"自己创建的"打归属标记，收藏歌单的属主是原作者
                "netease_uid": uid if p.get("creator", {}).get("userId") == uid else (p.get("creator") or {}).get("userId"),
            }
        )
    if metas:
        with get_conn() as conn:
            upsert_playlists(conn, metas)
    return raw


# ---------------------------------------------------------------------------
# 听歌排行
# ---------------------------------------------------------------------------

def get_user_play_records(uid: int, period: int, cookie: str) -> dict:
    """听歌排行（cache-aside）。period: 1 周 / 0 所有。

    返回 {"data": {"records": [{playCount, song:{...}}], "listenSongs": int|None}, "from_cache": bool}
    """
    with get_conn() as conn:
        cached = get_play_records_fresh(conn, uid, period, TTL_PLAY_RECORDS)
    if cached:
        records = [
            {
                "playCount": r["play_count"],
                "song": {
                    "id": r["song_id"],
                    "name": r["name"],
                    "ar": [{"name": a} for a in (r["artists"] or [])],
                    "al": {"name": r["album_name"]},
                },
            }
            for r in cached
        ]
        logger.info("听歌排行 uid=%s period=%s 命中缓存（%d 条）", uid, period, len(records))
        return {"data": {"records": records, "listenSongs": None}, "from_cache": True}

    # api-enhanced 路由规则：模块文件名下划线转斜杠（user_record.js → /user/record）
    # uid 为必选参数，缺省时网易按公开视角返回（playCount 恒为 0）
    raw = call_music_api("/user/record", cookie, {"uid": uid, "type": period})
    if raw.get("code") != 200:
        return {"data": {"records": [], "listenSongs": None, "raw": raw}, "from_cache": False}

    items = raw.get("weekData") if period == 1 else raw.get("allData") or []
    rows = [
        {"song_id": it.get("song", {}).get("id"), "play_count": it.get("playCount", 0)}
        for it in items
        if it.get("song", {}).get("id")
    ]
    with get_conn() as conn:
        upsert_songs(conn, [song_raw_to_row(it["song"]) for it in items if it.get("song", {}).get("id")])
        upsert_play_records(conn, uid, period, rows)
    sync_pending_embeddings()

    records = [
        {
            "playCount": it.get("playCount", 0),
            "song": {
                "id": it.get("song", {}).get("id"),
                "name": it.get("song", {}).get("name"),
                "ar": it.get("song", {}).get("ar"),
                "al": it.get("song", {}).get("al"),
                "dt": it.get("song", {}).get("dt"),
            },
        }
        for it in items
    ]
    return {
        "data": {"records": records, "listenSongs": raw.get("listenSongs")},
        "from_cache": False,
    }


# ---------------------------------------------------------------------------
# 歌曲批量详情
# ---------------------------------------------------------------------------

def get_songs_by_ids(ids: list[int], cookie: str) -> dict:
    """批量歌曲详情：PG 优先，缺的调 API 补齐并落库。"""
    ids = list(dict.fromkeys(i for i in ids if i))[:20]

    found: dict[int, dict] = {}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT song_id, name, artists, album_name, duration_ms, pop, cover_url FROM songs WHERE song_id = ANY(%s)",
            (ids,),
        ).fetchall()
    for r in rows:
        found[r["song_id"]] = {
            "id": r["song_id"],
            "name": r["name"],
            "ar": [{"name": a} for a in (r["artists"] or [])],
            "al": {"name": r["album_name"], "picUrl": r["cover_url"]},
            "dt": r["duration_ms"],
            "pop": r["pop"],
        }

    missing = [i for i in ids if i not in found]
    from_cache = not missing
    if missing:
        raw = call_music_api("/song/detail", cookie, {"ids": ",".join(map(str, missing))})
        if raw.get("code") == 200 and raw.get("songs"):
            cache_songs(raw["songs"])
            for s in raw["songs"]:
                if s.get("id"):
                    found[s["id"]] = s
        elif raw.get("code") != 200:
            return {"data": {"songs": list(found.values()), "raw_code": raw.get("code")}, "from_cache": from_cache}

    ordered = [found[i] for i in ids if i in found]
    return {"data": {"songs": ordered}, "from_cache": from_cache}
