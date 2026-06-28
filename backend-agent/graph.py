import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

# 导入我们之前写好的工具 (确保你在 tools 目录下有一个空的 __init__.py)
from tools.user_tools import get_user_account_info, get_user_subcount_info

# 1. 加载 .env 文件中的环境变量
load_dotenv()

# 2. 初始化大语言模型
# 因为配置了环境变量，这里不需要显式传入 url 和 key
llm = ChatOpenAI(
    model="deepseek-v4-flash",  # 如果用智谱，可以改成 "glm-4"
    temperature=0.7         # 稍微带点发散性，适合做聊天和推荐
)

# 3. 将技能（Tools）打包成列表
# 告诉 AI 它拥有哪些网易云超能力
tools = [
    get_user_account_info, 
    get_user_subcount_info
]

# 4. 构建 Agent 图谱
# create_react_agent 会自动帮我们处理：接收消息 -> 决定是否调工具 -> 执行工具 -> 整合答案返回
app_graph = create_react_agent(llm, tools)