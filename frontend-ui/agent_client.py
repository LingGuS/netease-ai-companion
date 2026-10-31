"""后台网络请求线程：SSE 流式读取后端 /chat/stream，防止卡死 UI。

信号协议：
  token        LLM 回复增量（逐块）
  tool_status  工具调用状态行（"🔧 正在调用 xxx..."）
  finished     结束事件（最终回复文本 + 卡片列表）
  error        错误信息
"""

import json

import requests
from PyQt6.QtCore import QThread, pyqtSignal

BACKEND = "http://127.0.0.1:8000"


class AgentWorker(QThread):
    token = pyqtSignal(str)
    tool_status = pyqtSignal(str)
    finished = pyqtSignal(str, list)  # （最终回复文本, 卡片列表）
    error = pyqtSignal(str)

    def __init__(self, message, session_id):
        super().__init__()
        self.message = message
        self.session_id = session_id

    def run(self):
        """线程启动后执行的核心逻辑：流式读取 SSE 事件。"""
        url = f"{BACKEND}/chat/stream"
        payload = {"message": self.message, "session_id": self.session_id}
        resp = None
        try:
            # 连接超时 10s；读超时放宽到 300s（SSE 长连接，工具执行期间无数据也属正常）
            with requests.post(url, json=payload, stream=True, timeout=(10, 300)) as resp:
                resp.raise_for_status()
                event = None
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        try:
                            data = json.loads(line.split(":", 1)[1].strip())
                        except ValueError:
                            continue
                        self._dispatch(event, data)
        except requests.exceptions.ConnectionError:
            self.error.emit("请求失败：无法连接到后端，请检查 FastAPI 是否已启动。")
        except requests.exceptions.Timeout:
            self.error.emit("请求失败：AI 思考超时。")
        except requests.exceptions.HTTPError:
            # 后端进入流之前返回的 4xx/5xx（如游客登录失败）
            try:
                detail = resp.json().get("detail")
            except Exception:
                detail = None
            self.error.emit(f"请求失败：{detail or f'后端返回 HTTP {resp.status_code}'}")
        except Exception as e:
            self.error.emit(f"请求失败：发生未知错误 {e}")

    def _dispatch(self, event, data):
        if event == "token":
            content = data.get("content", "")
            if content:
                self.token.emit(content)
        elif event == "tool":
            self.tool_status.emit(data.get("message", ""))
        elif event == "done":
            self.finished.emit(data.get("reply", ""), data.get("cards") or [])
        elif event == "error":
            self.error.emit(data.get("detail", "未知错误"))
