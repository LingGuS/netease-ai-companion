from PyQt6.QtCore import QThread, pyqtSignal
import requests

class AgentWorker(QThread):
    """
    后台网络请求线程
    专门负责向 FastAPI 后端发送请求，防止卡死 UI 界面
    """
    # 定义信号，用于在请求完成后把数据传回给主线程的 UI
    finished = pyqtSignal(str)  # 成功时发射回复文本
    error = pyqtSignal(str)     # 失败时发射错误信息

    def __init__(self, message, cookie=""):
        super().__init__()
        self.message = message
        self.cookie = cookie

    def run(self):
        """线程启动后执行的核心逻辑"""
        url = "http://127.0.0.1:8000/chat"
        payload = {
            "message": self.message,
            "user_cookie": self.cookie
        }
        
        try:
            # 发起 POST 请求，设置超时时间防止无限挂起
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status() # 如果是 4xx 或 5xx 错误，抛出异常
            
            data = response.json()
            reply_text = data.get("reply", "后端未返回 reply 字段")
            
            # 请求成功，发射 finished 信号
            self.finished.emit(reply_text)
            
        except requests.exceptions.ConnectionError:
            self.error.emit("请求失败：无法连接到后端，请检查 FastAPI 是否已启动。")
        except requests.exceptions.Timeout:
            self.error.emit("请求失败：AI 思考超时。")
        except Exception as e:
            self.error.emit(f"请求失败：发生未知错误 {str(e)}")