"""对话容器（右栏 / 纯对话态的合并大板块）。

由 chat_widget.py 重构：去掉顶栏（登录入口移至左栏用户信息），
气泡支持带头像/无头像两种模式（纯对话态带头像，播放态仅留消息文本），
流式 SSE 与非流式两条请求路径并存（设置里切换）。
"""

import json

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from agent_client import AgentWorker
from ui_theme import SCROLLBAR_QSS

BACKEND = "http://127.0.0.1:8000"

WELCOME_TEXT = "你好呀，我是你的音乐伴侣～可以直接说「来点适合现在心情的歌」，或者让我看看你的听歌口味。"


class ChatBubble(QWidget):
    """单条消息气泡；头像可随界面状态显隐（播放态无头像）。"""

    def __init__(self, text, is_user=False, avatar_text="AI"):
        super().__init__()
        self._stream_text = text
        self._is_user = is_user

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.avatar = QLabel(avatar_text if len(avatar_text) <= 2 else avatar_text[:1])
        self.avatar.setFixedSize(34, 34)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if is_user:
            self.avatar.setStyleSheet(
                "background-color: #E04E4E; border-radius: 17px; color: white; font-size: 13px;"
            )
        else:
            self.avatar.setStyleSheet(
                "background-color: #3B72D9; border-radius: 17px; color: white; font-size: 11px;"
            )

        self.bubble_label = QLabel(text)
        bubble = self.bubble_label
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setMaximumWidth(460)
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        if is_user:
            bubble.setStyleSheet("""
                QLabel {
                    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #E45454, stop:1 #C24040);
                    color: white; padding: 11px 15px;
                    border-radius: 14px; border-top-right-radius: 5px; font-size: 14px;
                }
            """)
        else:
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #414141; color: #E2E2E2; padding: 11px 15px;
                    border-radius: 14px; border-top-left-radius: 5px; font-size: 14px;
                }
            """)

        if is_user:
            layout.addStretch()
            layout.addWidget(bubble)
            layout.addWidget(self.avatar)
        else:
            layout.addWidget(self.avatar)
            layout.addWidget(bubble)
            layout.addStretch()

        self.setLayout(layout)

    def append_stream_text(self, chunk: str):
        """流式追加回复文本。"""
        self._stream_text += chunk
        self.bubble_label.setText(self._stream_text)

    def finish_stream(self, final_text: str):
        self.bubble_label.setText(final_text)

    def set_avatar_visible(self, visible: bool):
        self.avatar.setVisible(visible)

    def set_bubble_max_width(self, width: int):
        self.bubble_label.setMaximumWidth(width)


class ChatPanel(QWidget):
    """对话容器：头部（面板开关 + 会话标题 + 登录态）+ 气泡流 + 输入区。"""

    response_finished = pyqtSignal(str, list)  # (最终回复, 卡片列表)
    panel_toggle_requested = pyqtSignal()      # 请求展开/收起中栏播放面板

    def __init__(self, nam: QNetworkAccessManager, settings_store, session_id: str):
        super().__init__()
        self.setMinimumWidth(240)  # 三栏拖拽时的最小可用宽度
        self.nam = nam
        self.settings_store = settings_store
        self.session_id = session_id
        self._stream_bubble = None
        self._tool_status_label = None
        self._worker = None
        self._bubbles: list[ChatBubble] = []
        self._logged_in = False
        self._nickname = ""
        self._avatars_visible = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 头部：会话标题 + 登录态 ----
        header = QFrame()
        header.setFixedHeight(48)
        header.setStyleSheet("QFrame { background-color: transparent; border-bottom: 1px solid rgba(255,255,255,0.08); }")
        head_layout = QHBoxLayout(header)
        head_layout.setContentsMargins(22, 0, 22, 0)
        head_layout.setSpacing(8)
        self.panel_btn = QPushButton("♪")
        self.panel_btn.setCheckable(True)
        self.panel_btn.setFixedSize(26, 26)
        self.panel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.panel_btn.setToolTip("展开 / 收起播放面板")
        self.panel_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #A0A0A0;
                          font-size: 14px; border-radius: 8px; }
            QPushButton:hover { background-color: #3A3636; color: white; }
            QPushButton:checked { background-color: rgba(224,78,78,0.15); color: #F09090; }
        """)
        self.panel_btn.clicked.connect(self.panel_toggle_requested.emit)
        head_layout.addWidget(self.panel_btn)
        self.title_label = QLabel("新对话")
        self.title_label.setStyleSheet("color: #A0A0A0; font-size: 13px; background: transparent; border: none;")
        self.user_state_label = QLabel("游客态")
        self.user_state_label.setStyleSheet("color: #777171; font-size: 11px; background: transparent; border: none;")
        head_layout.addWidget(self.title_label)
        head_layout.addStretch()
        head_layout.addWidget(self.user_state_label)
        root.addWidget(header)

        # ---- 消息滚动区 ----
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }" + SCROLLBAR_QSS)
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.chat_layout = QVBoxLayout(self.scroll_content)
        self.chat_layout.setContentsMargins(24, 18, 24, 10)
        self.chat_layout.setSpacing(16)
        self.chat_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_content)
        root.addWidget(self.scroll_area, 1)

        # ---- 输入区 ----
        input_wrap = QWidget()
        input_wrap.setStyleSheet("background: transparent;")
        input_layout = QHBoxLayout(input_wrap)
        input_layout.setContentsMargins(24, 0, 24, 20)
        input_frame = QFrame()
        input_frame.setStyleSheet("""
            QFrame { background-color: #383535; border-radius: 14px; border: none; }
        """)
        frame_layout = QHBoxLayout(input_frame)
        frame_layout.setContentsMargins(17, 9, 9, 9)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("说点什么…比如「来点适合现在心情的歌」")
        self.input_edit.setStyleSheet(
            "QLineEdit { background: transparent; border: none; color: white; font-size: 14px; }"
        )
        self.input_edit.returnPressed.connect(self._send)
        self.send_btn = QPushButton()
        self.send_btn.setFixedSize(36, 36)
        self.send_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.send_btn.setText("➤")
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #E04E4E; color: white; border: none;
                border-radius: 11px; font-size: 15px;
            }
            QPushButton:hover { background-color: #F06060; }
            QPushButton:disabled { background-color: #8A4646; }
        """)
        self.send_btn.clicked.connect(self._send)
        frame_layout.addWidget(self.input_edit, 1)
        frame_layout.addWidget(self.send_btn)
        input_layout.addWidget(input_frame)
        root.addWidget(input_wrap)

        self.new_session(self.session_id)

    # ------------------------------------------------------------------
    # 会话与消息渲染
    # ------------------------------------------------------------------
    def _clear_bubbles(self):
        self._bubbles = []
        self._stream_bubble = None
        self._tool_status_label = None
        # 移除除弹簧外的所有行（气泡在布局里统一回收，避免重复 deleteLater）
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _append_bubble(self, text: str, is_user: bool) -> ChatBubble:
        avatar_text = "我" if is_user else "AI"
        if is_user and self._nickname:
            avatar_text = self._nickname[:1]
        bubble = ChatBubble(text, is_user, avatar_text=avatar_text)
        bubble.set_avatar_visible(self._avatars_visible)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self._bubbles.append(bubble)
        self._scroll_bottom()
        return bubble

    def _scroll_bottom(self):
        """滚动到底部：延迟到布局重算完成后再取 maximum（新气泡高度未算出时直接滚会滚不到位）。"""
        def _do():
            bar = self.scroll_area.verticalScrollBar()
            bar.setValue(bar.maximum())

        self.scroll_content.adjustSize()
        QTimer.singleShot(0, _do)

    def set_session(self, session_id: str):
        """切换当前会话（不发消息、不清界面，配合 load_messages 使用）。"""
        self.session_id = session_id

    def new_session(self, session_id: str):
        """新建对话：清空气泡，显示欢迎语。"""
        self.set_session(session_id)
        self.set_title("新对话")
        self._clear_bubbles()
        self._append_bubble(WELCOME_TEXT, is_user=False)

    def load_messages(self, messages: list):
        """回放历史会话消息（[{role, content}]，来自会话回放接口）。"""
        self._clear_bubbles()
        if not messages:
            self._append_bubble("（这个对话还没有消息记录）", is_user=False)
        for msg in messages:
            self._append_bubble(str(msg.get("content", "")), is_user=msg.get("role") == "user")

    def set_title(self, title: str):
        self.title_label.setText(title or "新对话")

    def set_panel_btn_checked(self, checked: bool):
        """同步面板开关的选中态（模式由外部切换时）。"""
        self.panel_btn.setChecked(checked)

    def set_user_state(self, logged_in: bool, nickname: str = ""):
        self._logged_in = logged_in
        self._nickname = nickname or ""
        self.user_state_label.setText(f"已登录 · {nickname}" if logged_in else "游客态")

    def set_avatars_visible(self, visible: bool):
        """切换气泡头像显隐（纯对话态 True / 播放态 False）。"""
        self._avatars_visible = visible
        for bubble in self._bubbles:
            bubble.set_avatar_visible(visible)

    def set_bubbles_max_width(self, width: int):
        for bubble in self._bubbles:
            bubble.set_bubble_max_width(width)

    # ------------------------------------------------------------------
    # 发送与响应
    # ------------------------------------------------------------------
    def _send(self):
        text = self.input_edit.text().strip()
        if not text:
            return

        self._append_bubble(text, is_user=True)
        self.input_edit.clear()
        self.send_btn.setEnabled(False)

        # 流式气泡 + 工具状态行
        self._stream_bubble = self._append_bubble("", is_user=False)
        self._tool_status_label = QLabel("")
        self._tool_status_label.setStyleSheet("color: #8A8580; font-size: 11px;")
        self._tool_status_label.setContentsMargins(15, 0, 0, 0)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._tool_status_label)
        self._scroll_bottom()

        use_stream = self.settings_store.value("stream", True, type=bool)
        if use_stream:
            self._worker = AgentWorker(message=text, session_id=self.session_id)
            self._worker.token.connect(self._on_stream_token)
            self._worker.tool_status.connect(self._on_tool_status)
            self._worker.finished.connect(self._on_response_finished)
            self._worker.error.connect(self._on_stream_error)
            self._worker.start()
        else:
            request = QNetworkRequest(QUrl(f"{BACKEND}/chat"))
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            body = json.dumps({"message": text, "session_id": self.session_id}).encode("utf-8")
            reply = self.nam.post(request, body)
            reply.finished.connect(lambda: self._on_blocking_reply(reply))

    def _on_blocking_reply(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._on_stream_error(f"请求失败：HTTP {reply.error()}")
                return
            data = json.loads(bytes(reply.readAll()).decode())
        except (ValueError, UnicodeDecodeError):
            self._on_stream_error("响应解析失败")
            return
        finally:
            reply.deleteLater()
        self._on_response_finished(data.get("reply", ""), data.get("cards") or [])

    def _on_stream_token(self, chunk: str):
        if self._stream_bubble is not None:
            self._stream_bubble.append_stream_text(chunk)
            self._scroll_bottom()

    def _on_tool_status(self, message: str):
        if self._tool_status_label is not None:
            self._tool_status_label.setText(message)
            self._scroll_bottom()

    def _on_response_finished(self, reply_text: str, cards: list):
        if self._stream_bubble is not None:
            self._stream_bubble.finish_stream(reply_text)
        self._clear_stream_ui()
        self._scroll_bottom()
        self.response_finished.emit(reply_text, cards or [])

    def _on_stream_error(self, error_text: str):
        self._clear_stream_ui()
        self._append_bubble(f"❌ {error_text}", is_user=False)

    def _clear_stream_ui(self):
        if self._tool_status_label is not None:
            self._tool_status_label.deleteLater()
            self._tool_status_label = None
        self._stream_bubble = None
        self._worker = None
        self.send_btn.setEnabled(True)
