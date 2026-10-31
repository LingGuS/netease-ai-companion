import base64
import json
import sys
import uuid

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QCursor
from agent_client import AgentWorker
from music_card import MusicCardWidget
from player import PlayerController

BACKEND = "http://127.0.0.1:8000"

class ChatBubble(QWidget):
    """自定义聊天气泡组件；AI 消息可携带音乐卡片"""

    def __init__(self, text, is_user=False, cards=None, player=None, nam=None):
        super().__init__()
        self._stream_text = text
        self._cards_rendered = False
        self._player = player
        self._nam = nam

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self._outer = outer

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
        self.bubble_label = QLabel(text)
        bubble = self.bubble_label
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        # 最大宽度限制，防止气泡过长
        bubble.setMaximumWidth(320)

        if is_user:
            # 用户消息：深灰色气泡，文字靠右，头像在右
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

        outer.addLayout(layout)

        # AI 消息携带的卡片（音乐播放卡等），排在文本气泡下方
        if not is_user and cards:
            self._render_cards(cards)

        self.setLayout(outer)

    def _render_cards(self, cards):
        """渲染卡片载荷到气泡下方（幂等）。"""
        if self._cards_rendered:
            return
        self._cards_rendered = True
        for card in cards:
            if card.get("type") == "music" and self._player is not None:
                self._outer.addWidget(MusicCardWidget(card.get("payload") or {}, self._player, self._nam))

    def append_stream_text(self, chunk: str):
        """流式追加回复文本。"""
        self._stream_text += chunk
        self.bubble_label.setText(self._stream_text)

    def finish_stream(self, final_text: str, cards=None):
        """流式结束：定稿文本并渲染卡片。"""
        self.bubble_label.setText(final_text)
        if cards:
            self._render_cards(cards)


class LoginDialog(QDialog):
    """扫码登录弹窗：居中显示二维码，2 秒轮询扫码状态"""

    def __init__(self, parent, nam: QNetworkAccessManager):
        super().__init__(parent)
        self.nam = nam
        self.login_id = None
        self._poll_inflight = False

        self.setWindowTitle("扫码登录")
        self.setFixedSize(300, 400)
        self.setStyleSheet("background-color: #2B2828;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        self.qr_label = QLabel("正在生成二维码...")
        self.qr_label.setFixedSize(240, 240)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setStyleSheet("background-color: white; border-radius: 8px; color: #666;")
        layout.addWidget(self.qr_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.status_label = QLabel("正在获取二维码...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #A0A0A0; font-size: 13px;")
        layout.addWidget(self.status_label)

        self.refresh_btn = QPushButton("刷新二维码")
        self.refresh_btn.setFixedHeight(30)
        self.refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #E04E4E; color: white; border: none;
                border-radius: 15px; font-size: 13px;
            }
            QPushButton:hover { background-color: #F06060; }
        """)
        self.refresh_btn.clicked.connect(self._start_login)
        self.refresh_btn.hide()
        layout.addWidget(self.refresh_btn)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_status)
        self._start_login()

    # ---- 流程 ----

    def _start_login(self):
        """向后端请求新二维码并开始轮询。"""
        self.qr_label.setText("正在生成二维码...")
        self.qr_label.setPixmap(QPixmap())
        self.status_label.setText("正在获取二维码...")
        self.refresh_btn.hide()

        request = QNetworkRequest(QUrl(f"{BACKEND}/login/qr/start"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self.nam.post(request, b"{}")
        reply.finished.connect(lambda: self._on_start_reply(reply))

    def _on_start_reply(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.status_label.setText("生成二维码失败，请检查后端服务")
                self.refresh_btn.show()
                return
            data = json.loads(bytes(reply.readAll()).decode())
        except (ValueError, UnicodeDecodeError):
            self.status_label.setText("响应解析失败")
            self.refresh_btn.show()
            return
        finally:
            reply.deleteLater()

        self.login_id = data.get("login_id")
        qrimg = data.get("qrimg") or ""
        pixmap = QPixmap()
        if qrimg and pixmap.loadFromData(base64.b64decode(qrimg.split(",", 1)[-1])):
            self.qr_label.setPixmap(
                pixmap.scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
            self.status_label.setText("请使用网易云音乐 App 扫码")
            self.timer.start(2000)
        else:
            self.status_label.setText("二维码加载失败")
            self.refresh_btn.show()

    def _poll_status(self):
        if self.login_id is None or self._poll_inflight:
            return
        self._poll_inflight = True
        request = QNetworkRequest(QUrl(f"{BACKEND}/login/qr/check?login_id={self.login_id}"))
        reply = self.nam.get(request)
        reply.finished.connect(lambda: self._on_poll_reply(reply))

    def _on_poll_reply(self, reply):
        self._poll_inflight = False
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return  # 网络抖动，下轮再试
            data = json.loads(bytes(reply.readAll()).decode())
        except (ValueError, UnicodeDecodeError):
            return
        finally:
            reply.deleteLater()

        status = data.get("status")
        if status == "waiting":
            self.status_label.setText("请使用网易云音乐 App 扫码")
        elif status == "confirmed":
            self.status_label.setText("已扫码，请在手机上确认")
        elif status in ("expired", "failed"):
            self.timer.stop()
            self.status_label.setText(data.get("message") or "二维码已过期")
            self.refresh_btn.show()
        elif status == "success":
            self.timer.stop()
            user = data.get("user") or {}
            self.status_label.setText(f"登录成功：{user.get('nickname', '')}")
            self.qr_label.setText("✓")
            self.qr_label.setStyleSheet("background-color: #3A3A3A; border-radius: 8px; color: #7BC47F; font-size: 60px;")
            QTimer.singleShot(600, self.accept)


class ChatWidget(QWidget):
    """主聊天窗口"""
    def __init__(self):
        super().__init__()
        # 1. 严格设置窗口大小为 472 * 704
        self.setFixedSize(372, 604)
        self.setWindowTitle("AI 音乐助手")
        
        # 设置整个窗口的基础背景色（网易云暗黑风）
        self.setStyleSheet("background-color: #2B2828; font-family: 'Microsoft YaHei';")

        # 会话号：应用启动时生成，整个生命周期复用（后端凭它续接多轮记忆）
        self.session_id = str(uuid.uuid4())

        # 共享播放器与封面网络加载器（所有音乐卡片复用）
        self.nam = QNetworkAccessManager(self)
        self.player = PlayerController(self, self.nam)
        self._stream_bubble = None
        self._tool_status_label = None
        self._logged_in = False

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

        # 登录按钮：未登录显示「登录」，已登录显示昵称
        self.login_btn = QPushButton("登录")
        self.login_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.login_btn.setFixedHeight(28)
        self.login_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #A0A0A0;
                border: 1px solid #555;
                border-radius: 14px;
                padding: 0 14px;
                font-size: 12px;
            }
            QPushButton:hover { color: white; border-color: #888; }
            QPushButton:disabled { color: #E2E2E2; border-color: #E04E4E; }
        """)
        self.login_btn.clicked.connect(self._on_login_clicked)

        top_layout.addWidget(title_label)
        top_layout.addStretch()
        top_layout.addWidget(self.login_btn)

        self.main_layout.addLayout(top_layout)
        self._refresh_login_state()

    def _refresh_login_state(self):
        """查询后端 /me，刷新顶栏登录态显示（启动与登录成功后共用）。"""
        request = QNetworkRequest(QUrl(f"{BACKEND}/me"))
        reply = self.nam.get(request)
        reply.finished.connect(lambda: self._on_me_loaded(reply))

    def _on_me_loaded(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = json.loads(bytes(reply.readAll()).decode())
        except (ValueError, UnicodeDecodeError):
            return
        finally:
            reply.deleteLater()
        if data.get("logged_in"):
            nickname = data.get("nickname") or "已登录"
            vip_tag = " · VIP" if data.get("vip_type") else ""
            self.login_btn.setText(f"{nickname}{vip_tag}")
            self.login_btn.setEnabled(True)  # 点击 = 登出（带确认）
            self._logged_in = True
        else:
            self.login_btn.setText("登录")
            self.login_btn.setEnabled(True)
            self._logged_in = False

    def _on_login_clicked(self):
        """未登录：打开居中扫码登录弹窗；已登录：确认后登出。"""
        if self._logged_in:
            reply = QMessageBox.question(
                self,
                "登出",
                "确定要登出当前账号吗？登出后将回到游客模式。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            request = QNetworkRequest(QUrl(f"{BACKEND}/logout"))
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            resp = self.nam.post(request, b"{}")
            resp.finished.connect(lambda: self._refresh_login_state())
            return
        dialog = LoginDialog(self, self.nam)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_login_state()

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

    def append_message(self, text, is_user, cards=None):
        """向聊天区添加一条新消息气泡（AI 消息可携带卡片）"""
        bubble = ChatBubble(text, is_user, cards=cards, player=self.player, nam=self.nam)
        # 将气泡插入到弹簧（Stretch）的上方
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self._scroll_bottom()

    def _scroll_bottom(self):
        """聊天区自动滚动到底部。"""
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
        self.send_btn.setEnabled(False) # 发送后暂时禁用按钮，防止连点

        # 2. 流式气泡 + 工具状态行，随 SSE 事件实时更新
        self._stream_bubble = ChatBubble("", is_user=False, player=self.player, nam=self.nam)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._stream_bubble)
        self._tool_status_label = QLabel("")
        self._tool_status_label.setStyleSheet("color: #8A8580; font-size: 11px;")
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._tool_status_label)
        self._scroll_bottom()

        # 3. 启动后台线程读取 SSE 流（登录态由后端自动解析）
        self.worker = AgentWorker(message=text, session_id=self.session_id)
        self.worker.token.connect(self.on_stream_token)
        self.worker.tool_status.connect(self.on_tool_status)
        self.worker.finished.connect(self.on_stream_finished)
        self.worker.error.connect(self.on_stream_error)
        self.worker.start() # 启动线程！

    def on_stream_token(self, chunk: str):
        if self._stream_bubble is not None:
            self._stream_bubble.append_stream_text(chunk)
            self._scroll_bottom()

    def on_tool_status(self, message: str):
        if self._tool_status_label is not None:
            self._tool_status_label.setText(message)
            self._scroll_bottom()

    def on_stream_finished(self, reply_text: str, cards: list):
        if self._stream_bubble is not None:
            self._stream_bubble.finish_stream(reply_text, cards or [])
        self._clear_stream_ui()
        self.send_btn.setEnabled(True)
        self._scroll_bottom()

    def on_stream_error(self, error_text: str):
        self._clear_stream_ui()
        self.append_message(f"❌ {error_text}", is_user=False)
        self.send_btn.setEnabled(True)

    def _clear_stream_ui(self):
        if self._tool_status_label is not None:
            self._tool_status_label.deleteLater()
            self._tool_status_label = None
        self._stream_bubble = None
        self.worker = None
        self.send_btn.setText("发送")

    def on_ai_error(self, error_text):
        """接收到请求错误后的槽函数（非流式路径 / 兜底）"""
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