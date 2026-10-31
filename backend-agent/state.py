"""Agent 图状态定义。

注意：这里故意不放 cookie / user_id —— state 会经 checkpointer 持久化，
凭证类信息一律走 RunnableConfig.configurable 通道按请求注入，不落盘。
"""

from typing import Annotated, NotRequired

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """ReAct Agent 的图状态（与 langgraph 内置 AgentState 保持同构）。"""

    messages: Annotated[list[BaseMessage], add_messages]
    remaining_steps: NotRequired[RemainingSteps]
