"""Agent 图谱构建工厂：LLM + 工具 + 系统提示词 + 外部注入的 checkpointer。

checkpointer 由 main.py 在数据层就绪后注入（AsyncPostgresSaver），
使对话记忆持久化到 PostgreSQL，重启不失忆。
"""

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from config import get_settings
from prompts import SYSTEM_PROMPT
from state import AgentState
from tools.history_tools import get_user_play_records
from tools.playlist_tools import get_playlist_detail, get_user_playlists
from tools.play_tools import control_player, play_song
from tools.profile_tools import analyze_my_taste
from tools.recommend_tools import (
    get_daily_recommend_songs,
    get_music_charts,
    get_recommend_playlists,
)
from tools.search_tools import search_music
from tools.semantic_tools import recommend_hybrid, search_songs_by_scene
from tools.song_tools import get_lyric, get_simi_song, get_song_detail
from tools.user_tools import get_user_account_info, get_user_subcount_info

settings = get_settings()

# 初始化大语言模型（配置统一来自 config，不再依赖隐式环境变量）
llm = ChatOpenAI(
    model=settings.llm_model,
    temperature=settings.llm_temperature,
    api_key=settings.llm_api_key,
    base_url=settings.llm_api_base,
)

# 将技能（Tools）按类别打包，告诉 AI 它拥有哪些网易云超能力
tools = [
    # 账号统计类
    get_user_account_info,
    get_user_subcount_info,
    # 歌单类
    get_playlist_detail,
    get_user_playlists,
    # 行为类
    get_user_play_records,
    # 搜索类
    search_music,
    search_songs_by_scene,
    # 推荐类
    get_daily_recommend_songs,
    get_recommend_playlists,
    get_music_charts,
    recommend_hybrid,
    # 歌曲类
    get_song_detail,
    get_lyric,
    get_simi_song,
    # 播放类
    control_player,
    play_song,
    # 画像类
    analyze_my_taste,
]


def build_prompt(state, config):
    """动态 system prompt：基础人设 + 可选的用户画像注入。

    画像文本由 main.py 经 configurable 通道按请求注入（登录用户才有）。
    """
    profile_text = ((config or {}).get("configurable") or {}).get("profile_text")
    system = SYSTEM_PROMPT
    if profile_text:
        system += (
            "\n\n## 当前用户画像（来自其真实听歌数据）\n"
            f"{profile_text}\n"
            "（推荐时优先贴合画像，让推荐更有'懂你'的感觉；"
            "但用户当场明确提出的需求永远优先于画像；"
            "引用画像要自然，不要逐字复述）"
        )
    return [{"role": "system", "content": system}, *state["messages"]]


def build_app_graph(checkpointer=None):
    """构建 Agent 图谱：接收消息 -> 决定是否调工具 -> 执行工具 -> 整合答案返回。"""
    return create_react_agent(
        llm,
        tools,
        prompt=build_prompt,
        state_schema=AgentState,
        checkpointer=checkpointer,
    )
