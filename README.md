# 网易云 AI 音乐助手（netease-ai-companion）

一个吸附在网易云音乐旁的桌面 AI 音乐助手：基于 LangGraph 的 ReAct Agent，能调用 16 个网易云技能（账号/歌单/听歌排行/搜索/推荐/播放/画像），结合用户真实听歌数据生成个性化回答，并支持卡片内播放音乐与反馈闭环。

## 功能总览

- **对话式音乐助手**：多轮对话 + 工具调用，回答基于真实网易云数据（不编造）
- **扫码登录**：顶栏扫码授权，cookie 持久化到 PostgreSQL（长期免登录）
- **个性化画像**：自动分析听歌排行 + 歌单 + 播放行为 + 赞/踩反馈，生成流派权重与画像文本，每次对话自动注入
- **混合推荐**：口味画像向量 + 场景语义 + 官方推荐 + 播放延伸四路召回，过滤已播放/已踩过的歌后加权排序
- **音乐播放卡片**：歌名/歌手/封面/播放暂停按钮，点赞踩反馈，播放行为回流画像
- **语义场景搜歌**："适合雨天的歌"这类模糊描述走向量检索（Milvus）

## 架构（七步演进）

```
前端 PyQt6 悬浮窗（吸附网易云窗口）
  ├─ 聊天气泡 + 流式渲染（SSE）+ 音乐卡片（播放/赞踩）
  └─ 扫码登录弹窗 / 顶栏登录态
        │ HTTP / SSE
后端 FastAPI（LangGraph ReAct Agent）
  ├─ 16 个技能工具（统一 MusicAPIClient + store 缓存编排层）
  ├─ 动态提示词（画像注入）+ PostgresSaver 多轮记忆
  ├─ 游客登录兜底 / 扫码登录（全局当前用户）
  └─ 推荐引擎（多源召回 → 过滤 → 加权排序）
        │
数据层
  ├─ PostgreSQL：歌曲/歌单/听歌排行/播放行为/反馈/画像/会话 + 对话 checkpoint
  └─ Milvus Lite：歌曲向量库（1439+ 首）+ 用户口味向量
        │
music-api（NeteaseCloudMusicApi api-enhanced，Node :3000）
```

七步演进：① 补地基（config 注入/多轮记忆/系统提示词）→ ② 补技能（12 工具）→ ③ 数据层（PG + Milvus + 向量化 + 缓存层）→ ③.5 播放卡片 + ④ 画像闭环 → ⑤ 向量能力应用 → ⑥ 混合推荐 → ⑦ 打磨（流式/登出/日志）。扫码登录从第 7 步提前实施。

## 快速开始

### 0. 环境要求

- Windows 10+、Python 3.13、Node.js 18+
- 本地 PostgreSQL 18（建库脚本自动创建 `netease_ai` 库）
- DeepSeek API Key（或其他 OpenAI 兼容服务）

### 1. 安装依赖

```powershell
python -m venv venv
E:\netease-ai-companion\venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 注意：pip 必须用 venv 的显式路径，否则可能装进全局 Python。

### 2. 配置 .env

复制 `.env.example` 为 `.env`，填入你的 API Key：

```
LLM_API_BASE="https://api.deepseek.com/v1"
LLM_API_KEY="sk-xxx"
```

数据库默认连接 `postgresql://postgres:postgres@127.0.0.1:5432/netease_ai`（首次启动自动建库建表），如需修改一并调整。

### 3. 启动 music-api

```powershell
cd music-api\api-enhanced
node app.js        # 入口是 app.js；默认端口 3000
```

### 4. 启动后端

```powershell
cd backend-agent
E:\netease-ai-companion\venv\Scripts\python.exe main.py    # 端口 8000，日志落盘 logs/backend.log
```

首次启动自动完成：建库建表（`sql/` 目录脚本）→ Milvus 集合初始化 → 下载 bge 向量模型（约 100MB，走 hf-mirror 镜像）→ 对话记忆表。

### 5. 启动前端

```powershell
cd frontend-ui
E:\netease-ai-companion\venv\Scripts\python.exe main.py
```

窗口自动吸附到网易云音乐右侧（需网易云处于打开状态）。点顶栏「登录」扫码后解锁个性化与播放。

### 6.（可选）扩充语义检索语料

```powershell
cd backend-agent
# 先停止后端（Milvus Lite 文件锁），跑完再启动
E:\netease-ai-companion\venv\Scripts\python.exe backfill_corpus.py
```

## 目录结构

```
├── sql/                  # 建表与迁移脚本（编号追加，幂等）
├── backend-agent/        # FastAPI 后端 + LangGraph Agent
│   ├── tools/            # 16 个技能工具（账号/歌单/排行/搜索/推荐/播放/画像/语义）
│   ├── db.py             # PG 连接与 upsert 辅助
│   ├── store.py          # 缓存编排层（cache-aside + TTL + 向量化挂钩）
│   ├── music_api.py      # music-api 统一客户端（重试/日志/缓存绕过）
│   ├── session.py        # cookie 解析链（登录用户 → 游客兜底）
│   ├── login.py          # 扫码登录流程
│   ├── taste_profile.py  # 画像计算（分源归一化融合）
│   ├── recommender.py    # 混合推荐引擎
│   ├── embedder.py       # bge-small-zh 向量化
│   └── milvus_client.py  # Milvus Lite 客户端
├── frontend-ui/          # PyQt6 悬浮窗
│   ├── chat_widget.py    # 聊天主窗（流式气泡/登录态/登出）
│   ├── music_card.py     # 音乐卡片（播放/赞踩）
│   ├── player.py         # 全局播放器
│   └── login_dialog      # 扫码登录弹窗
├── scripts/chat_smoke.py # 对话冒烟测试脚本
├── data/                 # Milvus Lite 本地库（gitignore）
└── logs/                 # 后端滚动日志（gitignore）
```

## 已知限制

- **游客播放受限**：网易云不给游客身份返回播放 URL（免费歌也一样），完整播放体验需扫码登录
- **单用户模型**：全局当前登录用户，多账号切换未实现
- **画像年代分布**（era_dist）未填充：需要专辑发行时间，列为后续项
- **听歌排行次数**：仅本人 cookie 可见真实次数（网易隐私机制），公开视角恒为 0
- **对话记忆**：后端重启后保留（PG 持久化），但前端每次启动生成新会话号，旧会话需同 session_id 才能续接
- **embed_text 极简版**：歌曲向量仅基于歌名/歌手/专辑，场景区分度有限；歌词/热评增强为后续优化项

## 冒烟测试

```powershell
E:\netease-ai-companion\venv\Scripts\python.exe scripts\chat_smoke.py test-1 "我的账号信息是什么？" --stream
```
