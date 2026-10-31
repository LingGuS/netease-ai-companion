"""语义检索类工具：场景搜歌与混合推荐（基于 Milvus 向量检索）。

与 search_music 的分工：歌名/歌手/专辑等**关键词明确**的用 search_music；
"适合雨天的歌""来点适合学习的"这类**场景/心情/氛围**模糊需求用本模块。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from embedder import embed_query
from milvus_client import search_songs
from recommender import recommend_hybrid as _recommend_hybrid

_GUEST_HINT = (
    "获取失败：当前是游客身份，没有口味画像，无法按口味推荐。"
    "可以改用 get_daily_recommend_songs 或 search_songs_by_scene 来满足用户。"
)


def _format_hit(hit: dict) -> str:
    """Milvus 冗余标量直接格式化（无需回查 PG）。"""
    entity = hit.get("entity") or {}
    artists = entity.get("artists") or "未知歌手"
    album = entity.get("album") or "未知专辑"
    return (
        f"《{entity.get('name', '未知')}》 - {artists} | 专辑：{album}"
        f" | id={hit.get('id')} | 相似度 {hit.get('distance', 0):.3f}"
    )


@tool
def search_songs_by_scene(query: str, limit: int = 10, config: RunnableConfig = None) -> str:
    """
    按场景/心情/氛围语义搜歌：把自然语言描述向量化后在全曲库中检索语义相近的歌曲。
    当用户的需求是场景或情绪类模糊描述时调用，例如"适合雨天的歌"、"来点适合学习的"、
    "安静的晚上听什么"、"运动时想听燃一点的"——这类需求用 search_music 关键词搜索往往搜不到，
    请优先使用本工具。结果带歌曲 id，可继续调用 play_song / get_lyric。
    (注意：游客身份也可使用)
    """
    limit = min(max(limit, 1), 20)

    try:
        vector = embed_query(query)
        hits = search_songs(vector, limit)
    except Exception as e:
        return f"语义搜歌失败（数据层）：{e}"

    if not hits:
        return f"没有在曲库中找到与「{query}」氛围相近的歌曲，可以换个描述再试。"

    lines = [f"{i}. {_format_hit(h)}" for i, h in enumerate(hits, 1)]
    return f"语义检索「{query}」的结果（按相似度排序）：\n" + "\n".join(lines)


@tool
def recommend_hybrid(limit: int = 10, scene_query: str = None, config: RunnableConfig = None) -> str:
    """
    混合推荐：融合口味画像、场景氛围、官方每日推荐与播放延伸四路召回，
    自动过滤最近播放过和你踩过的歌，按综合匹配度排序。
    当用户说"按我的口味推荐"、"懂我一点的推荐"时调用（不传 scene_query）；
    用户话术里带场景时提取传入，如"雨天按我口味来点" → scene_query="雨天"、"运动时按我品味放" → scene_query="运动"。
    返回带来源标签（口味画像/场景匹配/官方每日推荐/播放延伸）的候选，请结合来源写"为什么推荐给你"，
    歌曲带 id 可继续调用 play_song / get_lyric。
    (注意：需要真实账号登录；游客身份请改用 get_daily_recommend_songs)
    """
    limit = min(max(limit, 1), 20)
    cookie = config.get("configurable", {}).get("cookie", "") if config else ""

    try:
        result = _recommend_hybrid(cookie, limit=limit, scene_query=scene_query)
    except Exception as e:
        return f"混合推荐失败（数据层）：{e}"

    if result.get("error") == "guest":
        return _GUEST_HINT

    candidates = result.get("candidates") or []
    if not candidates:
        return "暂时没有合适的推荐候选，可以改用 get_daily_recommend_songs。"

    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. 《{c['name']}》 - {c['artists']} | 专辑：{c['album']}"
            f" | id={c['song_id']} | 来源：{'/'.join(c['sources'])} | 匹配度 {c['score']:.2f}"
        )
    return (
        "混合推荐候选（已过滤最近播放与踩过的歌，按综合匹配度排序）：\n" + "\n".join(lines)
    )
