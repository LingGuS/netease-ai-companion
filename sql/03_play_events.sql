-- ============================================================
-- 03: 播放行为表（第 4 步画像闭环）
-- 主动点击播放是最强的偏好信号；本期只落库，权重融合在第 6 步推荐排序
-- ============================================================

CREATE TABLE IF NOT EXISTS play_events (
    id          BIGSERIAL PRIMARY KEY,
    netease_uid BIGINT,                          -- 未登录为 NULL
    song_id     BIGINT NOT NULL REFERENCES songs(song_id) ON DELETE CASCADE,
    played_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_play_events_uid_time ON play_events(netease_uid, played_at);
