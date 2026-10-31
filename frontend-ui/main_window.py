"""主窗口：1618×916 三栏布局（选择容器 / 歌曲容器 / 对话容器）+ 双状态机。

状态约定（与 design/demo.html 一致）：
- MODE_CHAT 纯对话：中+右合并为一个大对话板块，气泡带头像；
- MODE_NOW  推荐/播放中：左=选择，中=歌曲容器，右=对话（无头像，仅消息文本）。
切换触发：/chat/stream 的 done 事件携带可播放音乐卡 → MODE_NOW。
"""

import json
import uuid

from PyQt6.QtCore import Qt, QSettings, QPoint, QUrl
from PyQt6.QtGui import QCursor, QGuiApplication
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from chat_panel import ChatPanel
from left_panel import LeftPanel
from player import PlayerController
from song_panel import SongPanel

BACKEND = "http://127.0.0.1:8000"

MODE_CHAT = "chat"  # 纯对话：中+右合并
MODE_NOW = "now"    # 推荐/播放中


class TitleBar(QWidget):
    """自绘标题栏：应用名 + 最小化/最大化/关闭，沿用深色主题；可拖动、双击最大化。"""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        self._drag_offset = None
        self.setFixedHeight(40)
        self.setStyleSheet("""
            QWidget { background-color: #232020; border-bottom: 1px solid rgba(255,255,255,0.08); }
            QLabel { background: transparent; border: none; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("AI 音乐伴侣")
        title.setStyleSheet("color: #A0A0A0; font-size: 13px;")
        layout.addWidget(title)
        layout.addStretch()

        self.min_btn = QPushButton("─")
        self.max_btn = QPushButton("□")
        self.close_btn = QPushButton("✕")
        for btn in (self.min_btn, self.max_btn):
            btn.setFixedSize(46, 40)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet("""
                QPushButton { background: transparent; border: none; color: #A0A0A0;
                              font-family: 'Segoe UI Symbol', 'Microsoft YaHei';
                              font-size: 12px; border-radius: 0; }
                QPushButton:hover { background-color: #3A3636; color: white; }
            """)
        self.close_btn.setFixedSize(46, 40)
        self.close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.close_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #A0A0A0;
                          font-size: 13px; border-radius: 0; }
            QPushButton:hover { background-color: #E04E4E; color: white; }
        """)
        self.min_btn.clicked.connect(self._window.showMinimized)
        self.max_btn.clicked.connect(self._toggle_max)
        self.close_btn.clicked.connect(self._window.close)
        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

    def _toggle_max(self):
        if self._window.isMaximized():
            self._window.showNormal()
            self.max_btn.setText("□")
        else:
            self._window.showMaximized()
            self.max_btn.setText("❐")

    # ---- 拖动窗口 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._window.isMaximized():
            self._drag_offset = (
                event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._toggle_max()
        super().mouseDoubleClickEvent(event)


class MainWindow(QWidget):
    MIN_WIDTH = 1080
    MIN_HEIGHT = 680

    def __init__(self):
        super().__init__()
        # 窗口尺寸：屏幕可用区域的 60%（宽高各取 60%，随屏幕比例自适应），小屏由最小尺寸兜底
        screen = QGuiApplication.primaryScreen().availableGeometry()
        width = max(int(screen.width() * 0.6), self.MIN_WIDTH)
        height = max(int(screen.height() * 0.6), self.MIN_HEIGHT)
        self.resize(width, height)
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.move(screen.center() - QPoint(width // 2, height // 2))
        self.setWindowTitle("AI 音乐伴侣")
        self.setStyleSheet("background-color: #2B2828;")

        self.settings_store = QSettings("netease-ai-companion", "frontend")
        self.nam = QNetworkAccessManager(self)
        self.player = PlayerController(self, self.nam)
        self.player.queue_changed.connect(self._push_queue)
        self.player.current_changed.connect(self._push_queue)
        self.session_id = str(uuid.uuid4())
        self.mode = MODE_CHAT

        # ---- 三栏 ----
        self.left_panel = LeftPanel(self.nam, self.settings_store)
        self.song_panel = SongPanel(self.nam)
        self.song_panel.attach_controller(self.player)
        self.chat_panel = ChatPanel(self.nam, self.settings_store, self.session_id)
        self.chat_panel.response_finished.connect(self._on_response_finished)
        self.chat_panel.panel_toggle_requested.connect(self._toggle_panel)

        # ---- 三栏：QSplitter 承载，交界处可拖拽自定义宽度 ----
        self._split_ready = False
        self.middle_holder = QWidget()
        lay_m = QVBoxLayout(self.middle_holder)
        lay_m.setContentsMargins(0, 0, 0, 0)
        lay_m.addWidget(self.song_panel)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStyleSheet("""
            QSplitter::handle { background: transparent; }
            QSplitter::handle:horizontal { width: 4px; }
            QSplitter::handle:hover, QSplitter::handle:pressed {
                background-color: rgba(224,78,78,0.35);
            }
        """)
        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.middle_holder)
        self.splitter.addWidget(self.chat_panel)
        self.splitter.setStretchFactor(0, 0)  # 左栏不随窗口自动变宽
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 2)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(TitleBar(self))
        root.addWidget(self.splitter, 1)

        # ---- 接线 ----
        self.left_panel.new_chat_requested.connect(self._new_chat)
        self.left_panel.session_selected.connect(self._load_session)
        self.left_panel.session_delete_requested.connect(self._delete_session)
        self.left_panel.login_requested.connect(self._open_login)
        self.left_panel.logout_requested.connect(self._logout)
        # quality/stream 变化写入 QSettings，播放器取 URL 与发送时各自读取，无需再接

        self.set_mode(MODE_CHAT)
        self._refresh_me()
        self._refresh_sessions()

    # ------------------------------------------------------------------
    # 双状态切换
    # ------------------------------------------------------------------
    def set_mode(self, mode: str):
        """切换界面状态：纯对话隐藏中栏（对话占满），播放态三栏齐现。

        宽度沿用用户拖拽后的值（左栏 264 / 中栏 700 为默认），不重置。
        """
        sizes = self.splitter.sizes()
        min_left = self.left_panel.minimumWidth()
        min_mid = self.song_panel.minimumWidth()
        left_w = sizes[0] if len(sizes) == 3 and sizes[0] >= min_left else 264
        mid_w = sizes[1] if len(sizes) == 3 and sizes[1] >= min_mid else 700

        self.mode = mode
        handles = self.splitter.handleWidth() * 2
        total = max(self.splitter.width(), 960)
        if mode == MODE_CHAT:
            self.middle_holder.hide()
            self.chat_panel.set_avatars_visible(True)
            self.chat_panel.set_bubbles_max_width(560)
            self.chat_panel.set_panel_btn_checked(False)
            self.splitter.setSizes([left_w, 0, max(total - left_w - handles, 320)])
        else:
            self.middle_holder.show()
            self.chat_panel.set_avatars_visible(False)
            self.chat_panel.set_bubbles_max_width(460)
            self.chat_panel.set_panel_btn_checked(True)
            self.splitter.setSizes([left_w, mid_w, max(total - left_w - mid_w - handles, 320)])

    def _toggle_panel(self):
        """对话头部的 ♪ 开关：手动展开/收起中栏播放面板。"""
        self.set_mode(MODE_NOW if self.mode == MODE_CHAT else MODE_CHAT)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 首次获得真实尺寸后，按当前模式重新分配三栏宽度
        if not self._split_ready:
            self._split_ready = True
            self.set_mode(self.mode)

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------
    def _new_chat(self):
        self.session_id = str(uuid.uuid4())
        self.left_panel.upsert_front(self.session_id, "新对话")
        self.chat_panel.new_session(self.session_id)
        self.set_mode(MODE_CHAT)

    def _load_session(self, session_id: str, title: str):
        # 不强制切回纯对话态：若正在播放，保持播放面板可见
        self.session_id = session_id
        self.chat_panel.set_session(session_id)
        self.chat_panel.set_title(title)

        request = QNetworkRequest(QUrl(f"{BACKEND}/sessions/{session_id}/messages"))
        reply = self.nam.get(request)
        reply.finished.connect(lambda: self._on_messages_loaded(reply, session_id))
        # 同时恢复该会话的播放队列（只填充抽屉，不自动播放）
        queue_request = QNetworkRequest(QUrl(f"{BACKEND}/sessions/{session_id}/queue"))
        queue_reply = self.nam.get(queue_request)
        queue_reply.finished.connect(lambda: self._on_queue_loaded(queue_reply, session_id))

    def _on_queue_loaded(self, reply, session_id: str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = json.loads(bytes(reply.readAll()).decode())
        except (ValueError, UnicodeDecodeError):
            return
        finally:
            reply.deleteLater()
        if session_id != self.session_id:
            return
        # 队列随会话切换：无队列的会话清空本地队列（原队列已持久化在各自会话里）
        self.player.restore(data.get("songs") or [], int(data.get("current_index", -1)))

    def _delete_session(self, session_id: str):
        """删除历史会话：后端清登记行/队列/记忆，成功后本地移除条目。"""
        request = QNetworkRequest(QUrl(f"{BACKEND}/sessions/{session_id}"))
        reply = self.nam.deleteResource(request)
        reply.finished.connect(lambda: self._on_session_deleted(reply, session_id))

    def _on_session_deleted(self, reply, session_id: str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return  # 删除失败静默（后端已打日志），列表保持原样
        finally:
            reply.deleteLater()
        self.left_panel.remove_session(session_id)
        if session_id == self.session_id:
            # 删除的是当前会话：切到全新对话
            self._new_chat()

    def _push_queue(self, *_):
        """播放队列/当前指向变更时全量推送后端（按会话持久化，失败静默）。"""
        if not self.session_id:
            return
        request = QNetworkRequest(QUrl(f"{BACKEND}/sessions/{self.session_id}/queue"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        body = json.dumps({
            "song_ids": [p.get("song_id") for p in self.player.queue],
            "current_index": self.player.current_index,
        }).encode("utf-8")
        self.nam.post(request, body)

    def _on_messages_loaded(self, reply, session_id: str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = json.loads(bytes(reply.readAll()).decode())
        except (ValueError, UnicodeDecodeError):
            return
        finally:
            reply.deleteLater()
        if session_id == self.session_id:
            self.chat_panel.load_messages(data.get("messages") or [])

    def _refresh_sessions(self):
        """刷新历史会话列表；本地未登记的新会话乐观置顶合并。"""
        request = QNetworkRequest(QUrl(f"{BACKEND}/sessions?limit=50"))
        reply = self.nam.get(request)
        reply.finished.connect(lambda: self._on_sessions_loaded(reply))

    def _on_sessions_loaded(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = json.loads(bytes(reply.readAll()).decode())
        except (ValueError, UnicodeDecodeError):
            return
        finally:
            reply.deleteLater()
        sessions = list(data.get("sessions") or [])
        if not any(s.get("session_id") == self.session_id for s in sessions):
            sessions.insert(0, {"session_id": self.session_id, "title": None})
        self.left_panel.set_sessions(sessions)
        self.left_panel.set_active_session(self.session_id)
        for s in sessions:
            if s.get("session_id") == self.session_id and s.get("title"):
                self.chat_panel.set_title(s["title"])

    # ------------------------------------------------------------------
    # 对话响应驱动状态机
    # ------------------------------------------------------------------
    def _on_response_finished(self, reply_text: str, cards: list):
        """done 事件：音乐卡入队开播（可播放则切播放态）；player_control 卡驱动播放器。"""
        for card in cards:
            ctype = card.get("type")
            if ctype == "music":
                index = self.player.append(card.get("payload") or {})
                self.player.play_index(index)
                self.set_mode(MODE_NOW)
            elif ctype == "player_control":
                action = (card.get("payload") or {}).get("action", "")
                self.player.handle_control(action)
                # 播放类指令在面板隐藏时把歌曲面板拉开（暂停/停止不打扰当前界面）
                if (
                    action in ("play", "next", "prev")
                    and self.mode == MODE_CHAT
                    and self.player.current_index >= 0
                ):
                    self.set_mode(MODE_NOW)
        self._refresh_sessions()

    # ------------------------------------------------------------------
    # 登录态
    # ------------------------------------------------------------------
    def _refresh_me(self):
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
            self.left_panel.set_user(nickname, data.get("vip_type"), data.get("avatar_url"))
            self.chat_panel.set_user_state(True, nickname)
        else:
            self.left_panel.set_guest()
            self.chat_panel.set_user_state(False)

    def _open_login(self):
        from login_dialog import LoginDialog  # noqa: PLC0415 — 延迟导入避免循环

        dialog = LoginDialog(self, self.nam)
        if dialog.exec():
            self._refresh_me()

    def _logout(self):
        answer = QMessageBox.question(
            self, "登出", "确定要登出当前账号吗？登出后将回到游客模式。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        request = QNetworkRequest(QUrl(f"{BACKEND}/logout"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        resp = self.nam.post(request, b"{}")
        resp.finished.connect(lambda: self._refresh_me())
