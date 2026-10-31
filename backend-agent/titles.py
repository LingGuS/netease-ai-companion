"""会话标题自动总结：首轮提问后用 LLM 归纳 6~12 字标题。

由 main.py 在首轮对话完成后以 fire-and-forget 任务调用，不阻塞响应；
任何失败都降级为「截取首问前 12 字」，保证历史列表总有标题可用。
"""

import logging

from langchain_openai import ChatOpenAI

from config import get_settings

logger = logging.getLogger(__name__)

_MAX_TITLE_LEN = 12


def _get_llm() -> ChatOpenAI:
    """标题模型：低温度、独立于对话主模型的轻量实例。"""
    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        temperature=0.2,
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_base,
        timeout=15,
    )


def _fallback_title(message: str) -> str:
    text = message.strip().replace("\n", " ")
    return text[:_MAX_TITLE_LEN] or "新对话"


def generate_title(first_message: str) -> str:
    """对首问做优化总结；LLM 失败时兜底截断。"""
    prompt = (
        "给下面的用户提问起一个简洁的对话标题，要求："
        f"不超过{_MAX_TITLE_LEN}个字，概括意图，不加引号、句号或任何解释，直接输出标题。\n\n"
        f"用户提问：{first_message[:200]}"
    )
    try:
        text = _get_llm().invoke([{"role": "user", "content": prompt}])
        title = str(text.content).strip().strip('"“”「」『』').splitlines()[0]
        if not title:
            return _fallback_title(first_message)
        return title[:_MAX_TITLE_LEN]
    except Exception as e:
        logger.warning("标题生成失败，使用截断兜底: %s", e)
        return _fallback_title(first_message)
