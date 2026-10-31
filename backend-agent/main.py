"""FastAPI 服务入口：对外暴露 /chat（兼容）、/chat/stream（SSE 流式）、登录与反馈端点。

lifespan 中完成数据层初始化：建库建表（PG）→ Milvus 集合 → PostgresSaver 对话记忆表。
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 国内网络直连 HuggingFace 常超时；必须在任何依赖导入 huggingface_hub 之前设置镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel

from config import get_settings
from db import (
    clear_user_cookie,
    get_conn,
    init_database,
    insert_play_event,
    set_feedback,
    upsert_session,
)
from graph import build_app_graph
from login import QRLoginError, check_qr_login, get_current_user, start_qr_login
from milvus_client import ensure_collections
from session import GuestLoginError, get_or_create_cookie
from taste_profile import ensure_profile

# Windows 默认的 ProactorEventLoop 不支持 psycopg 异步模式，必须切换为 Selector
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 日志：文件落盘（5MB×3 滚动）+ 控制台双输出；压制 httpx/huggingface 刷屏
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(
            _LOG_DIR / "backend.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        ),
        logging.StreamHandler(),
    ],
)
for _noisy in ("httpx", "httpcore", "huggingface_hub"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("netease-ai-companion")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据层，构建带持久化记忆的 Agent 图谱。"""
    init_database()
    ensure_collections()
    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
        await saver.setup()
        app.state.graph = build_app_graph(saver)
        logger.info("数据层初始化完成：PG 表结构 + Milvus 集合 + PostgresSaver 记忆表")
        yield


app = FastAPI(title="Netease AI Companion Backend", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str
    session_id: str


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    cards: list[dict] = []  # 通用卡片载荷（音乐播放卡等），由工具产出、后端抽取


class PlayEventRequest(BaseModel):
    song_id: int


class FeedbackRequest(BaseModel):
    song_id: int
    action: str  # "like" | "dislike"


def _extract_cards(messages: list) -> list[dict]:
    """从本轮消息中抽取工具产出的卡片。

    checkpointer 下 result 含全量历史消息，因此倒序扫描、
    碰到本轮输入的 HumanMessage 即停止，只取本轮工具产出。
    """
    cards: list[dict] = []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, ToolMessage):
            try:
                data = json.loads(msg.content)
            except (ValueError, TypeError):
                continue
            card = data.get("_card") if isinstance(data, dict) else None
            if card:
                cards.append(card)
    return list(reversed(cards))


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    logger.info("收到消息 [session=%s]: %s", request.session_id[:8], request.message)

    try:
        # 1. 取（或注册）该会话的游客登录态
        cookie = get_or_create_cookie(request.session_id)

        # 2. 会话登记落库（游客会话 user_id 为空，登录后回填）
        await asyncio.to_thread(upsert_session, request.session_id)

        # 3. 组装输入与请求级配置：
        #    - thread_id 让 checkpointer 自动续接同一会话的历史消息
        #    - cookie 经 configurable 注入工具，不进 state、不落 checkpoint
        inputs = {"messages": [HumanMessage(content=request.message)]}
        invocation_config = {
            "configurable": {
                "thread_id": request.session_id,
                "cookie": cookie,
            }
        }

        # 3.5 画像注入：登录用户经 config 通道携带 profile_text（24h TTL 懒重算）
        try:
            user = get_current_user()
            if user and user.get("netease_uid"):
                profile = await asyncio.to_thread(
                    ensure_profile, user["netease_uid"], user.get("cookie") or ""
                )
                if profile and profile.get("profile_text"):
                    invocation_config["configurable"]["profile_text"] = profile["profile_text"]
        except Exception as e:
            logger.warning("画像注入失败（不影响对话）: %s", e)

        # 4. 运行 Agent（ainvoke 异步调用，适合 FastAPI）
        result = await app.state.graph.ainvoke(inputs, config=invocation_config)

        # 5. messages 包含对话全过程，最后一条即 AI 的最终回答
        final_reply = result["messages"][-1].content
        cards = _extract_cards(result["messages"])

    except GuestLoginError as e:
        logger.error("游客登录失败: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("Agent 执行异常")
        raise HTTPException(status_code=500, detail="Agent 思考时出了点问题，请稍后再试")

    return ChatResponse(reply=final_reply, session_id=request.session_id, cards=cards)


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """SSE 流式对话：逐 token 推送回复 + 工具调用状态行，结束事件携带完整载荷。

    事件协议：
      event: token  data: {"content": "..."}        —— LLM 回复增量
      event: tool   data: {"message": "🔧 ..."}     —— 工具调用状态行
      event: done   data: {reply, cards, session_id} —— 结束（含卡片）
      event: error  data: {detail}                  —— 流内错误
    """
    logger.info("收到流式消息 [session=%s]: %s", request.session_id[:8], request.message)

    try:
        cookie = get_or_create_cookie(request.session_id)
    except GuestLoginError as e:
        raise HTTPException(status_code=503, detail=str(e))

    await asyncio.to_thread(upsert_session, request.session_id)

    inputs = {"messages": [HumanMessage(content=request.message)]}
    invocation_config = {"configurable": {"thread_id": request.session_id, "cookie": cookie}}
    try:
        user = get_current_user()
        if user and user.get("netease_uid"):
            profile = await asyncio.to_thread(
                ensure_profile, user["netease_uid"], user.get("cookie") or ""
            )
            if profile and profile.get("profile_text"):
                invocation_config["configurable"]["profile_text"] = profile["profile_text"]
    except Exception as e:
        logger.warning("画像注入失败（不影响对话）: %s", e)

    async def event_gen():
        final_state = None
        try:
            async for ev in app.state.graph.astream_events(
                inputs, config=invocation_config, version="v2"
            ):
                kind = ev["event"]
                if kind == "on_chat_model_stream":
                    content = ev["data"]["chunk"].content
                    if isinstance(content, str) and content:
                        yield _sse("token", {"content": content})
                elif kind == "on_tool_start":
                    yield _sse("tool", {"message": f"🔧 正在调用 {ev.get('name', '工具')}..."})
                elif kind == "on_chain_end":
                    output = ev.get("data", {}).get("output")
                    # 最后一次携带 messages 的 chain end 即整图终态
                    if isinstance(output, dict) and "messages" in output:
                        final_state = output
            messages = (final_state or {}).get("messages") or []
            reply = messages[-1].content if messages else ""
            cards = _extract_cards(messages)
            yield _sse("done", {"reply": reply, "cards": cards, "session_id": request.session_id})
        except Exception as e:
            logger.exception("流式对话异常")
            yield _sse("error", {"detail": f"Agent 思考时出了点问题：{e}"})

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/login/qr/start")
async def login_qr_start():
    """生成登录二维码，返回 base64 图片与轮询用 login_id。"""
    try:
        return start_qr_login()
    except Exception as e:
        logger.error("生成二维码失败: %s", e)
        raise HTTPException(status_code=502, detail=f"生成二维码失败：{e}")


@app.get("/login/qr/check")
async def login_qr_check(login_id: str):
    """轮询扫码状态；803 授权成功时后端已持久化 cookie。"""
    try:
        return check_qr_login(login_id)
    except Exception as e:
        logger.error("查询扫码状态失败: %s", e)
        raise HTTPException(status_code=502, detail=f"查询扫码状态失败：{e}")


@app.get("/me")
async def me():
    """当前登录用户信息（不含 cookie）；未登录返回 logged_in=false。"""
    user = get_current_user()
    if not user:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "nickname": user.get("nickname"),
        "vip_type": user.get("vip_type"),
        "netease_uid": user.get("netease_uid"),
    }


@app.post("/logout")
async def logout():
    """登出：清除当前用户的 cookie（账号行保留，重新扫码即恢复）。"""
    user = get_current_user()
    if user:
        def _clear():
            with get_conn() as conn:
                clear_user_cookie(conn, user["id"])

        await asyncio.to_thread(_clear)
        logger.info("用户 %s 已登出", user.get("nickname"))
    return {"ok": True}


@app.post("/play/event")
async def play_event(req: PlayEventRequest):
    """前端卡片实际开播时上报播放行为（play_events 落库，画像/推荐信号）。"""
    user = get_current_user()
    uid = user.get("netease_uid") if user else None
    cookie = (user or {}).get("cookie") or ""

    def _record():
        try:
            from store import get_songs_by_ids

            get_songs_by_ids([req.song_id], cookie)  # 确保 songs 行存在（外键）
            with get_conn() as conn:
                insert_play_event(conn, uid, req.song_id)
        except Exception as e:
            logger.warning("播放行为落库失败 song_id=%s: %s", req.song_id, e)

    await asyncio.to_thread(_record)
    return {"ok": True}


@app.post("/feedback")
async def feedback(req: FeedbackRequest):
    """卡片赞/踩反馈（切换式：重复提交同 action = 取消）。"""
    user = get_current_user()
    if not user:
        return {"ok": False, "message": "登录后才能记录偏好"}

    action = 1 if req.action == "like" else 0
    cookie = user.get("cookie") or ""

    def _apply():
        # 推荐候选可能来自 API（每日推荐/相似歌），本地 songs 表未必有该行——
        # 先确保歌曲入库（PG 优先，缺则调接口补齐），否则外键约束会拒绝
        from store import get_songs_by_ids

        get_songs_by_ids([req.song_id], cookie)
        with get_conn() as conn:
            return set_feedback(conn, user["netease_uid"], req.song_id, action)

    try:
        result = await asyncio.to_thread(_apply)
    except Exception as e:
        logger.warning("反馈落库失败 song_id=%s: %s", req.song_id, e)
        return {"ok": False, "message": "反馈记录失败，请稍后再试"}
    return {"ok": True, "result": result}


if __name__ == "__main__":
    print("🚀 AI 后端服务启动中...")
    config = uvicorn.Config(
        app, host=settings.backend_host, port=settings.backend_port, log_level="info"
    )
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        # uvicorn.run() 在 Windows 上强制使用 ProactorEventLoop（不支持 psycopg 异步），
        # 因此绕过它的循环工厂，显式用 SelectorEventLoop 驱动服务
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()
    else:
        server.run()
