-- ============================================================
-- 07：会话播放队列（前端播放队列持久化）
-- 歌曲元数据事实源在 songs 表；play_url 为 20 分钟时效签名链接，永不落库
-- 当前播放指向 queue_index 存在 sessions 表（0-based，NULL/负数 = 无）
-- ============================================================

CREATE TABLE IF NOT EXISTS session_queue (
    session_id TEXT NOT NULL,
    song_id    BIGINT NOT NULL REFERENCES songs(song_id) ON DELETE CASCADE,
    position   INT    NOT NULL DEFAULT 0,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, song_id)
);
CREATE INDEX IF NOT EXISTS idx_session_queue_session ON session_queue(session_id, position);

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS queue_index INT;
