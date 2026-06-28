import os

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from langchain_core.messages import HumanMessage

# 导入我们刚刚创建的图谱
from graph import app_graph 

app = FastAPI(title="Netease AI Companion Backend")

class ChatRequest(BaseModel):
    message: str
    user_cookie: str = ""

class ChatResponse(BaseModel):
    reply: str

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    user_msg = request.message
    os.environ["CURRENT_USER_COOKIE"] = request.user_cookie
    
    print(f"[后端日志] 收到消息: {user_msg}")
    
    try:
        # 1. 组装输入数据
        # LangGraph 默认需要 messages 列表
        inputs = {
            "messages": [HumanMessage(content=user_msg)]
        }
        
        # TODO: LangGraph 进阶设计
        # 以后我们需要把 cookie 传给 AgentState，让 Tool 可以拿到。
        # 暂时为了简单演示，我们可以在 Prompt 中暗示 AI 需要什么。
        
        # 2. 运行 Agent
        # ainvoke 是异步调用，适合 FastAPI
        result = await app_graph.ainvoke(inputs)
        
        # 3. 提取最终回复
        # result["messages"] 包含了对话全过程，最后一条通常是 AI 的最终回答
        final_reply = result["messages"][-1].content
        
    except Exception as e:
        final_reply = f"Agent 思考时脑瓜子嗡嗡的：{str(e)}"
        
    return ChatResponse(reply=final_reply)

if __name__ == "__main__":
    print("🚀 AI 后端服务启动中...")
    uvicorn.run(app, host="127.0.0.1", port=8000)