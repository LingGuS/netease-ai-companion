-- ============================================================
-- 06：用户头像（前端重构左栏「用户信息」用）
-- 扫码登录时从 /user/account 的 profile.avatarUrl 取得并随账号持久化
-- ============================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT;
