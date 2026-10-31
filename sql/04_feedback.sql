-- ============================================================
-- 04: 反馈表（第 6 步混合推荐）
-- 一人一歌一条最新反馈；重复点击同按钮 = 取消（行删除）
-- ============================================================

CREATE TABLE IF NOT EXISTS feedback (
    netease_uid BIGINT NOT NULL,
    song_id     BIGINT NOT NULL REFERENCES songs(song_id) ON DELETE CASCADE,
    action      SMALLINT NOT NULL,               -- 1=like 0=dislike
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (netease_uid, song_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_uid_action ON feedback(netease_uid, action);
