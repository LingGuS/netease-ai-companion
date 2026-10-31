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
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel

from config import get_settings
from db import (
    clear_user_cookie,
    get_conn,
    get_session_queue,
    get_session_title,
    init_database,
    insert_play_event,
    list_sessions,
    set_feedback,
    set_session_queue,
    set_session_title,
    upsert_session,
)
from graph import build_app_graph
from login import QRLoginError, check_qr_login, get_current_user, start_qr_login
from milvus_client import ensure_collections
from music_api import MusicAPIError, call_music_api
from session import GuestLoginError, get_or_create_cookie
from store import get_songs_by_ids
from taste_profile import ensure_profile
from titles import generate_title
from tools.play_tools import get_play_payload

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


class PlayURLRequest(BaseModel):
    song_id: int
    level: str = "exhigh"


class SessionQueueRequest(BaseModel):
    song_ids: list[int] = []
    current_index: int = -1


def _spawn_title_generation(session_id: str, messages: list) -> None:
    """首轮对话完成后，后台用 LLM 把首问总结为会话标题（fire-and-forget）。

    每个会话只生成一次；LLM 失败由 titles.generate_title 内部兜底截断。
    """
    humans = [m for m in messages if isinstance(m, HumanMessage)]
    if len(humans) != 1:
        return
    first_message = str(humans[0].content)

    async def _job():
        try:

            def _work():
                with get_conn() as conn:
                    if get_session_title(conn, session_id):
                        return None
                title = generate_title(first_message)
                with get_conn() as conn:
                    set_session_title(conn, session_id, title)
                return title

            title = await asyncio.to_thread(_work)
            if title:
                logger.info("会话 %s 标题已生成: %s", session_id[:8], title)
        except Exception as e:
            logger.warning("会话标题生成失败: %s", e)

    asyncio.create_task(_job())


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

    _chat_common_tail(request.session_id, result["messages"])
    return ChatResponse(reply=final_reply, session_id=request.session_id, cards=cards)


def _chat_common_tail(session_id: str, messages: list) -> None:
    """对话成功后的公共收尾：首轮触发会话标题总结。"""
    _spawn_title_generation(session_id, messages)


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
            _chat_common_tail(request.session_id, messages)
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
    """当前登录用户信息（不含 cookie）；未登录返回 logged_in=false。

    avatar_url 为空（旧登录早于头像列）时自动从 /user/account 回填一次并落库。
    """
    user = get_current_user()
    if not user:
        return {"logged_in": False}

    avatar_url = user.get("avatar_url")
    if not avatar_url:
        try:
            account = await asyncio.to_thread(
                call_music_api, "/user/account", user.get("cookie") or ""
            )
            profile = (account.get("profile") or {}) if account.get("code") == 200 else {}
            avatar_url = profile.get("avatarUrl")

            def _save():
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE users SET avatar_url = %s WHERE id = %s",
                        (avatar_url, user["id"]),
                    )

            if avatar_url:
                await asyncio.to_thread(_save)
                logger.info("已回填用户 %s 的头像", user.get("nickname"))
        except Exception as e:
            logger.warning("回填头像失败（不影响登录态返回）: %s", e)

    return {
        "logged_in": True,
        "nickname": user.get("nickname"),
        "vip_type": user.get("vip_type"),
        "netease_uid": user.get("netease_uid"),
        "avatar_url": avatar_url,
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


@app.get("/sessions")
async def sessions_endpoint(limit: int = 50):
    """历史会话列表（最近活跃优先；标题为空由前端显示「新对话」）。"""
    return {"sessions": list_sessions(min(max(limit, 1), 200))}


@app.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str):
    """会话消息回放：从 checkpointer 读该 thread 的完整历史（过滤工具消息）。"""
    try:
        state = await app.state.graph.aget_state(
            {"configurable": {"thread_id": session_id}}
        )
    except Exception:
        logger.exception("读取会话历史失败 session=%s", session_id[:8])
        raise HTTPException(status_code=500, detail="读取会话历史失败")
    messages = []
    for m in (state.values or {}).get("messages") or []:
        if isinstance(m, HumanMessage):
            messages.append({"role": "user", "content": str(m.content)})
        elif isinstance(m, AIMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip():
                messages.append({"role": "assistant", "content": content})
    return {"session_id": session_id, "messages": messages}


def _queue_payloads(song_ids: list[int], cookie: str) -> list[dict]:
    """按队列顺序组装恢复用 payload；元数据全部来自 songs 表，不取 play_url。"""
    res = get_songs_by_ids(song_ids, cookie)
    by_id = {s.get("id"): s for s in res["data"]["songs"]}
    payloads = []
    for sid in song_ids:
        song = by_id.get(sid)
        if not song:
            continue
        payloads.append({
            "song_id": sid,
            "name": song.get("name") or f"歌曲 {sid}",
            "artists": [a.get("name") for a in song.get("ar") or []],
            "album": (song.get("al") or {}).get("name"),
            "cover_url": (song.get("al") or {}).get("picUrl"),
            "duration_ms": song.get("dt") or 0,
            "playable": False,
            "play_url": None,
        })
    return payloads


@app.get("/sessions/{session_id}/queue")
async def get_session_queue_endpoint(session_id: str):
    """恢复会话播放队列（切历史会话时前端拉取；只填充不自动播放）。"""
    def _work():
        with get_conn() as conn:
            return get_session_queue(conn, session_id)

    song_ids, current_index = await asyncio.to_thread(_work)
    if not song_ids:
        return {"session_id": session_id, "songs": [], "current_index": -1}
    user = get_current_user()
    cookie = (user or {}).get("cookie") or ""
    try:
        payloads = await asyncio.to_thread(_queue_payloads, song_ids, cookie)
    except Exception:
        logger.exception("恢复会话队列失败 session=%s", session_id[:8])
        raise HTTPException(status_code=500, detail="恢复播放队列失败")
    return {"session_id": session_id, "songs": payloads, "current_index": current_index}


@app.post("/sessions/{session_id}/queue")
async def set_session_queue_endpoint(session_id: str, req: SessionQueueRequest):
    """前端播放队列变更时全量推送（≤100 首，delete+insert 幂等；失败静默降级）。"""
    song_ids = req.song_ids[:100]

    def _work():
        with get_conn() as conn:
            set_session_queue(conn, session_id, song_ids, req.current_index)

    try:
        await asyncio.to_thread(_work)
    except Exception as e:
        logger.warning("保存会话队列失败 session=%s: %s", session_id[:8], e)
        return {"ok": False, "message": "播放队列保存失败"}
    return {"ok": True}


@app.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    """删除会话：登记行、播放队列与对话记忆（checkpoint thread）一并清除。"""
    def _work():
        with get_conn() as conn:
            conn.execute("DELETE FROM session_queue WHERE session_id = %s", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))

    try:
        await asyncio.to_thread(_work)
        checkpointer = getattr(app.state.graph, "checkpointer", None)
        if checkpointer is not None and hasattr(checkpointer, "delete_thread"):
            # AsyncPostgresSaver 的同步方法要求在非事件循环线程调用
            await asyncio.to_thread(checkpointer.delete_thread, session_id)
        logger.info("会话 %s 已删除", session_id[:8])
    except Exception:
        logger.exception("删除会话失败 session=%s", session_id[:8])
        raise HTTPException(status_code=500, detail="删除会话失败")
    return {"ok": True}


@app.post("/play/url")
async def play_url(req: PlayURLRequest):
    """前端直取播放链接（上一首/下一首/列表点播），不经过 LLM。

    与播放卡片同构的 payload；游客仍返回 playable=false 的降级载荷。
    """
    user = get_current_user()
    cookie = (user or {}).get("cookie") or ""
    try:
        payload = await asyncio.to_thread(get_play_payload, req.song_id, req.level, cookie)
    except MusicAPIError as e:
        raise HTTPException(status_code=502, detail=f"获取播放链接失败：{e}")
    except Exception:
        logger.exception("播放链接获取异常 song_id=%s", req.song_id)
        raise HTTPException(status_code=500, detail="获取播放链接失败")
    return payload


@app.get("/lyric/{song_id}")
async def lyric_endpoint(song_id: int):
    """歌词（标准 [mm:ss] LRC 文本），驱动前端流动歌词逐行高亮。

    用经典 /lyric 而非 /lyric/new：后者返回 JSON 行富文本格式，前端解析不了。
    """
    user = get_current_user()
    cookie = (user or {}).get("cookie") or ""
    try:
        data = await asyncio.to_thread(call_music_api, "/lyric", cookie, {"id": song_id})
    except MusicAPIError as e:
        raise HTTPException(status_code=502, detail=f"歌词获取失败：{e}")
    return {
        "song_id": song_id,
        "lrc": ((data.get("lrc") or {}).get("lyric")) or "",
        "tlyric": ((data.get("tlyric") or {}).get("lyric")) or "",
    }


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
