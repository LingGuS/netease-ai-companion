-- ============================================================
-- netease_ai 业务表建表脚本（幂等，可重复执行）
-- LangGraph 的 checkpoint 表由 AsyncPostgresSaver.setup() 自建，不在此文件
-- 约定：后续表结构变更按 02_、03_... 序号新增脚本，不修改本文件历史
-- ============================================================

-- 歌曲主表：全系统的事实源，Milvus 向量索引由它派生
CREATE TABLE IF NOT EXISTS songs (
    song_id     BIGINT PRIMARY KEY,             -- 网易云歌曲 id
    name        TEXT   NOT NULL,
    artists     JSONB  NOT NULL DEFAULT '[]',   -- ["周杰伦","方文山"]
    artist_ids  JSONB  NOT NULL DEFAULT '[]',   -- [6452, 6453]
    album_name  TEXT,
    album_id    BIGINT,
    duration_ms INT,
    pop         SMALLINT,                       -- 热度 0-100
    fee         SMALLINT,                       -- 0 免费 / 1 VIP / 4 购买专辑 / 8 低音质试听
    embed_text  TEXT,                           -- 向量化输入文本（歌名+歌手+专辑）
    embedded    BOOLEAN NOT NULL DEFAULT false, -- 是否已写入 Milvus
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 歌单表（公共榜单/推荐歌单的 netease_uid 为空；用户歌单记录属主）
CREATE TABLE IF NOT EXISTS playlists (
    playlist_id      BIGINT PRIMARY KEY,
    name             TEXT   NOT NULL,
    creator_nickname TEXT,
    track_count      INT,
    play_count       BIGINT,
    description      TEXT,
    netease_uid      BIGINT,
    synced_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_playlists_uid ON playlists(netease_uid);

-- 歌单-曲目关联（同步歌单时按歌单整批重建）
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id BIGINT NOT NULL,
    song_id     BIGINT NOT NULL REFERENCES songs(song_id) ON DELETE CASCADE,
    position    INT    NOT NULL DEFAULT 0,
    PRIMARY KEY (playlist_id, song_id)
);

-- 听歌排行缓存（第 4 步用户画像的直接数据源）
CREATE TABLE IF NOT EXISTS play_records (
    netease_uid BIGINT   NOT NULL,
    song_id     BIGINT   NOT NULL REFERENCES songs(song_id) ON DELETE CASCADE,
    period      SMALLINT NOT NULL,               -- 0 所有时间 / 1 最近一周
    play_count  INT      NOT NULL DEFAULT 0,
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (netease_uid, song_id, period)
);
CREATE INDEX IF NOT EXISTS idx_play_records_uid_period ON play_records(netease_uid, period);

-- 网易云账号档案（第 7 步扫码登录后写入；游客不入此表；cookie 为敏感字段）
CREATE TABLE IF NOT EXISTS users (
    id           SERIAL PRIMARY KEY,
    netease_uid  BIGINT UNIQUE,
    nickname     TEXT,
    vip_type     SMALLINT DEFAULT 0,
    cookie       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 前端会话登记（游客会话 user_id 为空，登录后回填；session_id 由前端 UUID 生成）
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    user_id        INT REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active_at);

-- 用户画像（本步建表，第 4 步由听歌数据计算填充；taste 向量在 Milvus user_taste 集合）
CREATE TABLE IF NOT EXISTS user_profiles (
    netease_uid      BIGINT PRIMARY KEY,
    favorite_genres  JSONB NOT NULL DEFAULT '{}',  -- {"民谣": 0.4, "摇滚": 0.3}
    favorite_artists JSONB NOT NULL DEFAULT '[]',
    era_dist         JSONB NOT NULL DEFAULT '{}',  -- {"2000s": 0.5}
    profile_text     TEXT,                         -- 注入 prompt 的自然语言画像
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
