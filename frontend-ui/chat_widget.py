import sys
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QScrollArea, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QCursor
from agent_client import AgentWorker

class ChatBubble(QWidget):
    """自定义聊天气泡组件"""
    def __init__(self, text, is_user=False):
        super().__init__()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 头像占位符
        avatar = QLabel()
        avatar.setFixedSize(36, 36)
        # 根据发送者设置不同的头像颜色以示区分
        if is_user:
            avatar.setStyleSheet("background-color: #E04E4E; border-radius: 18px;")
        else:
            avatar.setStyleSheet("background-color: #3B72D9; border-radius: 18px;")

        # 文本气泡
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        # 最大宽度限制，防止气泡过长
        bubble.setMaximumWidth(320)
        
        if is_user:
            # 用户消息：绿色或深灰色气泡，文字靠右，头像在右
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #4A4A4A;
                    color: white;
                    padding: 12px;
                    border-radius: 12px;
                    font-size: 14px;
                }
            """)
            layout.addStretch() # 左侧加弹簧，把气泡挤到右边
            layout.addWidget(bubble)
            layout.addWidget(avatar)
        else:
            # AI 消息：类似网易云的深灰色气泡，头像在左
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #383535;
                    color: #E2E2E2;
                    padding: 12px;
                    border-radius: 12px;
                    font-size: 14px;
                }
            """)
            layout.addWidget(avatar)
            layout.addWidget(bubble)
            layout.addStretch() # 右侧加弹簧

        self.setLayout(layout)


class ChatWidget(QWidget):
    """主聊天窗口"""
    def __init__(self):
        super().__init__()
        # 1. 严格设置窗口大小为 472 * 704
        self.setFixedSize(372, 604)
        self.setWindowTitle("AI 音乐助手")
        
        # 设置整个窗口的基础背景色（网易云暗黑风）
        self.setStyleSheet("background-color: #2B2828; font-family: 'Microsoft YaHei';")

        # 主垂直布局
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(15, 15, 15, 15)
        self.main_layout.setSpacing(10)

        self._init_top_bar()
        self._init_chat_area()
        self._init_input_area()

        # 添加一条初始 AI 欢迎语
        self.append_message("你好！我是你的 AI 音乐助手，快来跟我聊聊你今天想听什么歌吧？", is_user=False)

    def _init_top_bar(self):
        """初始化顶部标题栏"""
        top_layout = QHBoxLayout()
        
        
        title_label = QLabel("AI 音乐助手")
        title_label.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        
        top_layout.addWidget(title_label)
        top_layout.addStretch()
        
        self.main_layout.addLayout(top_layout)

    def _init_chat_area(self):
        """初始化可滚动的聊天记录区"""
        # 滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea { border: none; background-color: transparent; }
            QScrollBar:vertical {
                border: none; background: transparent; width: 6px; margin: 0px 0 0px 0;
            }
            QScrollBar::handle:vertical {
                background-color: #555; min-height: 20px; border-radius: 3px;
            }
        """)

        # 存放所有气泡的容器 Widget
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        
        self.chat_layout = QVBoxLayout(self.scroll_content)
        self.chat_layout.setContentsMargins(0, 10, 0, 10)
        self.chat_layout.setSpacing(20)
        self.chat_layout.addStretch() # 把消息往上顶

        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)

    def _init_input_area(self):
        """初始化底部输入区（包含输入框和发送按钮）"""
        # 输入区的外框（圆角背景）
        input_frame = QFrame()
        input_frame.setFixedHeight(90)
        input_frame.setStyleSheet("""
            QFrame {
                background-color: #383535;
                border-radius: 12px;
            }
        """)
        
        input_layout = QVBoxLayout(input_frame)
        input_layout.setContentsMargins(15, 10, 15, 10)
        
        # 文本输入框
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("说点什么吧...")
        self.input_edit.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: white;
                font-size: 14px;
            }
        """)
        # 绑定回车键发送
        self.input_edit.returnPressed.connect(self.handle_send_click)
        
        # 底部工具栏（字数提示 + 发送按钮）
        bottom_tool_layout = QHBoxLayout()
        bottom_tool_layout.setContentsMargins(0, 0, 0, 0)
        
        char_count = QLabel("1000")
        char_count.setStyleSheet("color: #666; font-size: 12px;")
        
        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(60, 28)
        self.send_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #A0A0A0;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover { color: white; }
        """)
        self.send_btn.clicked.connect(self.handle_send_click)
        
        bottom_tool_layout.addWidget(char_count)
        bottom_tool_layout.addStretch()
        bottom_tool_layout.addWidget(self.send_btn)
        
        input_layout.addWidget(self.input_edit)
        input_layout.addLayout(bottom_tool_layout)
        
        self.main_layout.addWidget(input_frame)

    def append_message(self, text, is_user):
        """向聊天区添加一条新消息气泡"""
        bubble = ChatBubble(text, is_user)
        # 将气泡插入到弹簧（Stretch）的上方
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        
        # 自动滚动到底部
        self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        )

    def handle_send_click(self):
        """点击发送按钮的处理逻辑"""
        text = self.input_edit.text().strip()
        if not text:
            return
            
        # 1. 在 UI 上显示用户发出的消息
        self.append_message(text, is_user=True)
        self.input_edit.clear()
        
        # 2. TODO: 这里留出调用后端的接口位置
        # 通常这里会触发一个异步任务或向 local websocket/http 发送请求
        # 比如： agent_client.send_to_ai(text, self.on_ai_reply)
        self.append_message(text, is_user=True)
        self.input_edit.clear()
        self.send_btn.setEnabled(False) # 发送后暂时禁用按钮，防止连点
        self.send_btn.setText("思考中...")
        
        my_real_cookie = ""
        # 2. 启动后台线程向 FastAPI 发送请求
        self.worker = AgentWorker(message=text , cookie=my_real_cookie)
        self.worker.finished.connect(self.on_ai_reply)
        self.worker.error.connect(self.on_ai_error)
        self.worker.start() # 启动线程！
        
    def on_ai_reply(self, reply_text):
        self.append_message(reply_text, is_user=False)
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")

    def on_ai_error(self, error_text):
        """接收到请求错误后的槽函数"""
        # 可以用特殊的格式显示错误，这里为了简单直接当普通消息发出来
        self.append_message(f"❌ {error_text}", is_user=False)
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")        

# ==========================================
# 如果直接运行此文件，将独立启动该 UI 用于测试
# ==========================================
""" if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = ChatWidget()
    window.show()
    sys.exit(app.exec()) """