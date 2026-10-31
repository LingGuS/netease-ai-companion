"""数据库连接管理与初始化：建库、执行 sql/ 脚本、业务表常用读写辅助。

业务查询使用 psycopg3 同步短连接（工具是同步函数，本地 PG 开销毫秒级）；
AsyncPostgresSaver 在 main.py 中自建连接，与此处互不干扰。
"""

import logging
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from config import get_settings

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _admin_url() -> str:
    """连到 postgres 系统库的连接串（仅用于判断/创建业务库）。"""
    base = get_settings().database_url
    return base.rsplit("/", 1)[0] + "/postgres"


def _dbname() -> str:
    return get_settings().database_url.rsplit("/", 1)[1].split("?")[0]


def ensure_database() -> None:
    """业务库不存在则创建（CREATE DATABASE 不能在事务内，需 autocommit）。"""
    dbname = _dbname()
    with psycopg.connect(_admin_url(), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{dbname}"')
            logger.info("已创建数据库 %s", dbname)


def run_sql_scripts() -> None:
    """按文件名顺序执行 sql/ 目录下的全部脚本（脚本本身幂等）。"""
    url = get_settings().database_url
    for script in sorted(SQL_DIR.glob("*.sql")):
        with psycopg.connect(url) as conn:
            conn.execute(script.read_text(encoding="utf-8"))
        logger.info("已执行 SQL 脚本 %s", script.name)


def init_database() -> None:
    ensure_database()
    run_sql_scripts()


def get_conn() -> psycopg.Connection:
    """业务查询短连接（调用方用 with 管理，退出自动提交/回滚）。"""
    return psycopg.connect(get_settings().database_url, row_factory=dict_row)


# ---------------------------------------------------------------------------
# upsert 辅助：入参均为 store.py 映射好的行 dict
# ---------------------------------------------------------------------------

_UPSERT_SONG = """
INSERT INTO songs (song_id, name, artists, artist_ids, album_name, album_id,
                   duration_ms, pop, fee, embed_text, cover_url)
VALUES (%(song_id)s, %(name)s, %(artists)s, %(artist_ids)s, %(album_name)s,
        %(album_id)s, %(duration_ms)s, %(pop)s, %(fee)s, %(embed_text)s, %(cover_url)s)
ON CONFLICT (song_id) DO UPDATE SET
    name = EXCLUDED.name, artists = EXCLUDED.artists, artist_ids = EXCLUDED.artist_ids,
    album_name = EXCLUDED.album_name, album_id = EXCLUDED.album_id,
    duration_ms = EXCLUDED.duration_ms, pop = EXCLUDED.pop, fee = EXCLUDED.fee,
    embed_text = EXCLUDED.embed_text, cover_url = EXCLUDED.cover_url, updated_at = now()
"""


def upsert_songs(conn: psycopg.Connection, rows: list[dict]) -> None:
    """批量 upsert 歌曲；embedded 标记不在冲突时重置。"""
    for row in rows:
        row = dict(row)
        row["artists"] = Json(row["artists"])
        row["artist_ids"] = Json(row["artist_ids"])
        conn.execute(_UPSERT_SONG, row)


def upsert_playlists(conn: psycopg.Connection, rows: list[dict]) -> None:
    """批量 upsert 歌单元数据。"""
    for row in rows:
        conn.execute(
            """
            INSERT INTO playlists (playlist_id, name, creator_nickname, track_count,
                                   play_count, description, netease_uid)
            VALUES (%(playlist_id)s, %(name)s, %(creator_nickname)s, %(track_count)s,
                    %(play_count)s, %(description)s, %(netease_uid)s)
            ON CONFLICT (playlist_id) DO UPDATE SET
                name = EXCLUDED.name, creator_nickname = EXCLUDED.creator_nickname,
                track_count = EXCLUDED.track_count, play_count = EXCLUDED.play_count,
                description = EXCLUDED.description, netease_uid = EXCLUDED.netease_uid,
                synced_at = now()
            """,
            row,
        )


def replace_playlist_tracks(conn: psycopg.Connection, playlist_id: int, song_ids: list[int]) -> None:
    """按歌单整批重建曲目关联。"""
    conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = %s", (playlist_id,))
    for pos, song_id in enumerate(song_ids):
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, song_id, position) VALUES (%s, %s, %s)",
            (playlist_id, song_id, pos),
        )


def upsert_play_records(conn: psycopg.Connection, uid: int, period: int, rows: list[dict]) -> None:
    """批量 upsert 听歌排行。rows: [{song_id, play_count}]"""
    for row in rows:
        conn.execute(
            """
            INSERT INTO play_records (netease_uid, song_id, period, play_count)
            VALUES (%(uid)s, %(song_id)s, %(period)s, %(play_count)s)
            ON CONFLICT (netease_uid, song_id, period) DO UPDATE SET
                play_count = EXCLUDED.play_count, synced_at = now()
            """,
            {"uid": uid, "period": period, **row},
        )


def upsert_session(session_id: str) -> None:
    """登记/续活前端会话（游客会话 user_id 为空）。"""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sessions (session_id) VALUES (%s)
            ON CONFLICT (session_id) DO UPDATE SET last_active_at = now()
            """,
            (session_id,),
        )


def get_session_title(conn: psycopg.Connection, session_id: str) -> str | None:
    """读取会话标题（未生成过返回 None）。"""
    row = conn.execute(
        "SELECT title FROM sessions WHERE session_id = %s", (session_id,)
    ).fetchone()
    return (row or {}).get("title")


def set_session_title(conn: psycopg.Connection, session_id: str, title: str) -> None:
    """写入会话标题（只在尚未生成时写入，避免覆盖）。"""
    conn.execute(
        "UPDATE sessions SET title = %s WHERE session_id = %s AND title IS NULL",
        (title, session_id),
    )


def list_sessions(limit: int = 50) -> list[dict]:
    """前端历史对话列表：最近活跃的会话（标题为空由前端显示「新对话」）。"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT session_id, title, last_active_at
            FROM sessions
            ORDER BY last_active_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    for row in rows:
        row["last_active_at"] = row["last_active_at"].isoformat()
    return rows


def set_session_queue(conn: psycopg.Connection, session_id: str, song_ids: list[int], current_index: int) -> None:
    """整体替换会话播放队列并记录当前指向。

    前端每次队列变更全量推送（≤100 首），delete+insert 天然幂等，无需处理位移。
    """
    conn.execute("DELETE FROM session_queue WHERE session_id = %s", (session_id,))
    for pos, song_id in enumerate(song_ids):
        conn.execute(
            """
            INSERT INTO session_queue (session_id, song_id, position)
            VALUES (%s, %s, %s)
            ON CONFLICT (session_id, song_id) DO NOTHING
            """,
            (session_id, song_id, pos),
        )
    # upsert 而非裸 UPDATE：会话尚未登记（未发过消息）时也能存下指向；
    # 只更新 queue_index，不动 last_active_at（避免播放行为重排历史列表）
    conn.execute(
        """
        INSERT INTO sessions (session_id, queue_index)
        VALUES (%s, %s)
        ON CONFLICT (session_id) DO UPDATE SET queue_index = EXCLUDED.queue_index
        """,
        (session_id, current_index),
    )


def get_session_queue(conn: psycopg.Connection, session_id: str) -> tuple[list[int], int]:
    """读取会话播放队列（按 position 有序）与当前指向。"""
    rows = conn.execute(
        "SELECT song_id FROM session_queue WHERE session_id = %s ORDER BY position",
        (session_id,),
    ).fetchall()
    row = conn.execute(
        "SELECT queue_index FROM sessions WHERE session_id = %s", (session_id,)
    ).fetchone()
    index = (row or {}).get("queue_index")
    return [r["song_id"] for r in rows], (index if index is not None else -1)


def upsert_user(conn: psycopg.Connection, row: dict) -> None:
    """登录成功后持久化用户与 cookie（长期存储，防频繁登录风控）。"""
    row = dict(row)
    row.setdefault("avatar_url", None)
    conn.execute(
        """
        INSERT INTO users (netease_uid, nickname, vip_type, cookie, avatar_url)
        VALUES (%(netease_uid)s, %(nickname)s, %(vip_type)s, %(cookie)s, %(avatar_url)s)
        ON CONFLICT (netease_uid) DO UPDATE SET
            nickname = EXCLUDED.nickname, vip_type = EXCLUDED.vip_type,
            cookie = EXCLUDED.cookie, avatar_url = EXCLUDED.avatar_url,
            last_seen_at = now()
        """,
        row,
    )


def get_current_user(conn: psycopg.Connection) -> dict | None:
    """全局当前登录用户：最近持有有效 cookie 的账号。"""
    return conn.execute(
        """
        SELECT id, netease_uid, nickname, vip_type, cookie, avatar_url
        FROM users
        WHERE cookie IS NOT NULL AND cookie <> ''
        ORDER BY last_seen_at DESC
        LIMIT 1
        """
    ).fetchone()


def clear_user_cookie(conn: psycopg.Connection, user_pk: int) -> None:
    """登出：清除该用户的 cookie（账号行与昵称历史保留，重新扫码即恢复）。"""
    conn.execute("UPDATE users SET cookie = NULL WHERE id = %s", (user_pk,))


def insert_play_event(conn: psycopg.Connection, uid: int | None, song_id: int) -> None:
    """追加播放行为（前端实际开播时上报）。"""
    conn.execute(
        "INSERT INTO play_events (netease_uid, song_id) VALUES (%s, %s)",
        (uid, song_id),
    )


def get_recent_play_events(conn: psycopg.Connection, uid: int, days: int = 7) -> list[dict]:
    """近期主动播放聚合（歌曲维度，供画像参考与第 6 步推荐排序）。"""
    return conn.execute(
        """
        SELECT song_id, count(*) AS play_times, max(played_at) AS last_played
        FROM play_events
        WHERE netease_uid = %s AND played_at >= now() - (%s || ' days')::interval
        GROUP BY song_id
        ORDER BY play_times DESC, last_played DESC
        """,
        (uid, str(days)),
    ).fetchall()


def get_profile(conn: psycopg.Connection, uid: int) -> dict | None:
    """读取用户画像（含 updated_at 供 TTL 判断）。"""
    return conn.execute(
        "SELECT *, updated_at FROM user_profiles WHERE netease_uid = %s",
        (uid,),
    ).fetchone()


def save_profile(conn: psycopg.Connection, row: dict) -> None:
    """写入/刷新用户画像。row: {netease_uid, favorite_genres, favorite_artists, era_dist, profile_text}"""
    row = dict(row)
    row["favorite_genres"] = Json(row.get("favorite_genres") or {})
    row["favorite_artists"] = Json(row.get("favorite_artists") or [])
    row["era_dist"] = Json(row.get("era_dist") or {})
    conn.execute(
        """
        INSERT INTO user_profiles (netease_uid, favorite_genres, favorite_artists,
                                   era_dist, profile_text)
        VALUES (%(netease_uid)s, %(favorite_genres)s, %(favorite_artists)s,
                %(era_dist)s, %(profile_text)s)
        ON CONFLICT (netease_uid) DO UPDATE SET
            favorite_genres = EXCLUDED.favorite_genres,
            favorite_artists = EXCLUDED.favorite_artists,
            era_dist = EXCLUDED.era_dist,
            profile_text = EXCLUDED.profile_text,
            updated_at = now()
        """,
        row,
    )


def set_feedback(conn: psycopg.Connection, uid: int, song_id: int, action: int) -> str:
    """切换式反馈：相同 action 重复提交 = 取消（删行），否则 upsert。

    返回 "liked" / "disliked" / "canceled"。
    """
    existing = conn.execute(
        "SELECT action FROM feedback WHERE netease_uid = %s AND song_id = %s",
        (uid, song_id),
    ).fetchone()
    if existing and existing["action"] == action:
        conn.execute(
            "DELETE FROM feedback WHERE netease_uid = %s AND song_id = %s",
            (uid, song_id),
        )
        return "canceled"
    conn.execute(
        """
        INSERT INTO feedback (netease_uid, song_id, action)
        VALUES (%s, %s, %s)
        ON CONFLICT (netease_uid, song_id) DO UPDATE SET
            action = EXCLUDED.action, created_at = now()
        """,
        (uid, song_id, action),
    )
    return "liked" if action == 1 else "disliked"


def get_feedback_filter(conn: psycopg.Connection, uid: int, dislike_days: int = 30) -> tuple[set, set]:
    """推荐过滤用：返回 (liked_song_ids, 30 天内 disliked_song_ids)。"""
    liked = conn.execute(
        "SELECT song_id FROM feedback WHERE netease_uid = %s AND action = 1",
        (uid,),
    ).fetchall()
    disliked = conn.execute(
        """
        SELECT song_id FROM feedback
        WHERE netease_uid = %s AND action = 0
          AND created_at >= now() - (%s || ' days')::interval
        """,
        (uid, str(dislike_days)),
    ).fetchall()
    return {r["song_id"] for r in liked}, {r["song_id"] for r in disliked}


def get_playlist_fresh(conn: psycopg.Connection, playlist_id: int, ttl_seconds: int) -> dict | None:
    """取未过期的歌单缓存；过期或不存在返回 None。"""
    row = conn.execute(
        "SELECT *, synced_at FROM playlists WHERE playlist_id = %s", (playlist_id,)
    ).fetchone()
    if not row:
        return None
    if (row["synced_at"].timestamp() + ttl_seconds) < time.time():
        return None
    track_rows = conn.execute(
        """
        SELECT pt.position, s.* FROM playlist_tracks pt
        JOIN songs s ON s.song_id = pt.song_id
        WHERE pt.playlist_id = %s ORDER BY pt.position
        """,
        (playlist_id,),
    ).fetchall()
    row["tracks"] = track_rows
    return row


def get_play_records_fresh(
    conn: psycopg.Connection, uid: int, period: int, ttl_seconds: int
) -> list[dict] | None:
    """取未过期的听歌排行缓存；过期或无数据返回 None。"""
    row = conn.execute(
        "SELECT max(synced_at) AS latest FROM play_records WHERE netease_uid = %s AND period = %s",
        (uid, period),
    ).fetchone()
    if not row or not row["latest"]:
        return None
    if (row["latest"].timestamp() + ttl_seconds) < time.time():
        return None
    return conn.execute(
        """
        SELECT s.song_id, s.name, s.artists, s.album_name, pr.play_count
        FROM play_records pr JOIN songs s ON s.song_id = pr.song_id
        WHERE pr.netease_uid = %s AND pr.period = %s
        ORDER BY pr.play_count DESC
        """,
        (uid, period),
    ).fetchall()
