"""左栏「选择容器」：新建对话栏 / 历史对话列表 / 用户信息（含设置弹层）。

高度约定：新建对话栏与用户信息栏均为 76px（歌曲容器的底部播放条与之对齐）。
历史对话标题来自后端（首轮提问的 LLM 总结），为空时前端显示「新对话」。
"""

from datetime import datetime

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor, QPainter, QPainterPath, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from login_dialog import LoginDialog
from ui_theme import SCROLLBAR_QSS

BACKEND = "http://127.0.0.1:8000"


def relative_time(iso_str: str) -> str:
    """把后端 ISO 时间戳转成列表里的相对时间（刚刚/N分钟前/昨天/日期）。"""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return ""
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    seconds = (now - dt).total_seconds()
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)}分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)}小时前"
    if seconds < 172800:
        return "昨天"
    return dt.strftime("%m-%d")


def circle_pixmap(source: QPixmap, size: int) -> QPixmap:
    """把任意图片裁成圆形头像。"""
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    painter.drawPixmap(
        0, 0,
        source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                      Qt.TransformationMode.SmoothTransformation),
    )
    painter.end()
    return out


# ---------------------------------------------------------------------------
# 新建对话栏（左一）
# ---------------------------------------------------------------------------
class NewChatBar(QWidget):
    new_chat_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFixedHeight(76)
        self.setStyleSheet("background-color: #232020; border-bottom: 1px solid rgba(255,255,255,0.08);")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        self.btn = QPushButton("＋  新建对话")
        self.btn.setFixedHeight(42)
        self.btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #E04E4E, stop:1 #C43F3F);
                color: white; border: none; border-radius: 11px; font-size: 14px;
            }
            QPushButton:hover { background-color: #F06060; }
        """)
        self.btn.clicked.connect(self.new_chat_requested.emit)
        layout.addWidget(self.btn)


# ---------------------------------------------------------------------------
# 历史对话列表（左二）
# ---------------------------------------------------------------------------
class _HistoryRow(QWidget):
    clicked = pyqtSignal(str, str)  # (session_id, title)
    delete_requested = pyqtSignal(str)  # (session_id,)

    def __init__(self, session_id: str, title: str, time_text: str):
        super().__init__()
        self.session_id = session_id
        self.title = title
        self._active = False
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 8, 9)
        layout.setSpacing(6)
        info = QVBoxLayout()
        info.setSpacing(3)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #E2E2E2; font-size: 13px; background: transparent;")
        self.time_label = QLabel(time_text)
        self.time_label.setStyleSheet("color: #6E6868; font-size: 11px; background: transparent;")
        info.addWidget(self.title_label)
        info.addWidget(self.time_label)
        layout.addLayout(info, 1)

        # 悬停行时显示的删除按钮
        self.delete_btn = QPushButton("✕")
        self.delete_btn.setFixedSize(20, 20)
        self.delete_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.delete_btn.setToolTip("删除该对话")
        self.delete_btn.hide()
        self.delete_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #8A8580;
                          font-size: 11px; border-radius: 6px; }
            QPushButton:hover { background-color: #4A3A3A; color: #F09090; }
        """)
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.session_id))
        layout.addWidget(self.delete_btn, 0, Qt.AlignmentFlag.AlignTop)
        self._apply_style()

    def _apply_style(self):
        if self._active:
            self.setStyleSheet("""
                QWidget { background-color: #373232; border-radius: 10px;
                          border-left: 3px solid #E04E4E; }
            """)
            self.title_label.setStyleSheet("color: white; font-size: 13px; background: transparent;")
        else:
            self.setStyleSheet("QWidget:hover { background-color: #2E2A2A; border-radius: 10px; }")
            self.title_label.setStyleSheet("color: #E2E2E2; font-size: 13px; background: transparent;")

    def set_active(self, active: bool):
        self._active = active
        self._apply_style()

    def enterEvent(self, event):
        self.delete_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.delete_btn.hide()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.session_id, self.title)
        super().mouseReleaseEvent(event)


class HistoryList(QWidget):
    session_selected = pyqtSignal(str, str)  # (session_id, title)
    delete_requested = pyqtSignal(str)       # (session_id,)

    def __init__(self):
        super().__init__()
        self._rows: list[_HistoryRow] = []
        self._sessions: list[dict] = []  # 最近一次 set_sessions 的原始数据（删除时保留时间戳）
        self._active_id = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            "QScrollArea { border: none; background-color: transparent; }" + SCROLLBAR_QSS
        )
        self.content = QWidget()
        self.content.setStyleSheet("background-color: transparent;")
        self.list_layout = QVBoxLayout(self.content)
        self.list_layout.setContentsMargins(10, 10, 10, 10)
        self.list_layout.setSpacing(2)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)

    def set_sessions(self, sessions: list):
        """全量刷新历史列表（rows: [{session_id, title, last_active_at}]）。"""
        self._sessions = [dict(s) for s in sessions]
        for row in self._rows:
            row.deleteLater()
        self._rows = []
        for data in self._sessions:
            row = _HistoryRow(
                data.get("session_id", ""),
                data.get("title") or "新对话",
                relative_time(data.get("last_active_at", "")),
            )
            row.clicked.connect(self._on_row_clicked)
            row.delete_requested.connect(self.delete_requested.emit)
            row.set_active(data.get("session_id") == self._active_id)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self._rows.append(row)

    def upsert_front(self, session_id: str, title: str):
        """新建对话后本地置顶（乐观插入，后端刷新时会合并）。"""
        sessions = [s for s in self._sessions if s["session_id"] != session_id]
        sessions.insert(0, {"session_id": session_id, "title": title, "last_active_at": None})
        self.set_sessions(sessions)
        self.set_active(session_id)

    def remove_session(self, session_id: str):
        """本地移除一条历史记录（保留其余条目的原始时间戳）。"""
        self.set_sessions([s for s in self._sessions if s.get("session_id") != session_id])
        if self._active_id == session_id:
            self._active_id = None

    def set_active(self, session_id: str):
        self._active_id = session_id
        for row in self._rows:
            row.set_active(row.session_id == session_id)

    def _on_row_clicked(self, session_id: str, title: str):
        self.set_active(session_id)
        self.session_selected.emit(session_id, title)


# ---------------------------------------------------------------------------
# 设置弹层（从用户信息栏向上弹出）
# ---------------------------------------------------------------------------
class SettingsPopover(QDialog):
    quality_changed = pyqtSignal(str)
    stream_changed = pyqtSignal(bool)
    login_requested = pyqtSignal()
    logout_requested = pyqtSignal()

    def __init__(self, settings_store, logged_in: bool, parent=None):
        super().__init__(parent)
        self.settings_store = settings_store
        self.setWindowTitle("设置")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(272)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("设置")
        title.setStyleSheet("color: #A0A0A0; font-size: 12px; background: transparent;")
        root.addWidget(title)

        # 音质
        quality_row = QHBoxLayout()
        quality_row.setSpacing(6)
        self._quality_group = QButtonGroup(self)
        self._quality_group.setExclusive(True)
        current_quality = settings_store.value("quality", "exhigh")
        for key, label in (("standard", "标准"), ("exhigh", "较高"), ("lossless", "无损")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == current_quality)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet("""
                QPushButton {
                    border: 1px solid rgba(255,255,255,0.12); background: transparent;
                    color: #A0A0A0; border-radius: 9px; padding: 5px 12px; font-size: 12px;
                }
                QPushButton:checked { border-color: #E04E4E; color: #F09090;
                                      background-color: rgba(224,78,78,0.12); }
            """)
            self._quality_group.addButton(btn)
            btn.clicked.connect(lambda _=False, k=key: self._on_quality(k))
            quality_row.addWidget(btn)
        quality_row.addStretch()
        root.addLayout(quality_row)

        # 开关
        self.stream_check = QCheckBox("流式回复")
        self.stream_check.setChecked(settings_store.value("stream", True, type=bool))
        self.stream_check.toggled.connect(self._on_stream)
        self.stream_check.setStyleSheet("""
            QCheckBox { color: #E2E2E2; font-size: 12.5px; background: transparent; spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
                                   border: 1px solid rgba(255,255,255,0.25); background: #4A4646; }
            QCheckBox::indicator:checked { background-color: #E04E4E; border-color: #E04E4E; }
        """)
        root.addWidget(self.stream_check)

        # 分隔线 + 登录/登出
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: rgba(255,255,255,0.1);")
        root.addWidget(line)

        self.account_btn = QPushButton("扫码登录" if not logged_in else "退出登录")
        self.account_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.account_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #D98383;
                          font-size: 12.5px; text-align: left; padding: 2px 0; }
            QPushButton:hover { color: #F09090; }
        """)
        if logged_in:
            self.account_btn.clicked.connect(self.logout_requested.emit)
        else:
            self.account_btn.clicked.connect(self.login_requested.emit)
        root.addWidget(self.account_btn)

    def _on_quality(self, key: str):
        self.settings_store.setValue("quality", key)
        self.quality_changed.emit(key)

    def _on_stream(self, checked: bool):
        self.settings_store.setValue("stream", checked)
        self.stream_changed.emit(checked)


# ---------------------------------------------------------------------------
# 用户信息栏（左三）
# ---------------------------------------------------------------------------
class UserBar(QWidget):
    quality_changed_sig = pyqtSignal(str)
    stream_changed_sig = pyqtSignal(bool)
    settings_requested = pyqtSignal(object)  # SettingsPopover 实例
    login_requested = pyqtSignal()
    logout_requested = pyqtSignal()

    def __init__(self, nam: QNetworkAccessManager, settings_store):
        super().__init__()
        self.nam = nam
        self.settings_store = settings_store
        self.setFixedHeight(76)
        self.setStyleSheet("background-color: #232020; border-top: 1px solid rgba(255,255,255,0.08);")
        self._logged_in = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 14, 0)
        layout.setSpacing(11)

        self.avatar_label = QLabel("客")
        self.avatar_label.setFixedSize(38, 38)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setStyleSheet(
            "background-color: #5A5252; border-radius: 19px; color: white; font-size: 15px;"
        )
        layout.addWidget(self.avatar_label)

        info = QHBoxLayout()
        info.setSpacing(7)
        self.name_label = QLabel("游客")
        self.name_label.setStyleSheet("color: white; font-size: 14px; background: transparent;")
        self.vip_label = QLabel("")
        self.vip_label.setStyleSheet("""
            QLabel { color: #E7B96B; background-color: rgba(231,185,107,0.14);
                     border-radius: 7px; padding: 1.5px 7px; font-size: 10px; }
        """)
        self.vip_label.hide()
        info.addWidget(self.name_label, 0, Qt.AlignmentFlag.AlignVCenter)
        info.addWidget(self.vip_label, 0, Qt.AlignmentFlag.AlignVCenter)
        info.addStretch()
        layout.addLayout(info, 1)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(30, 30)
        self.settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.settings_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #A0A0A0;
                          font-family: 'Segoe UI Symbol'; font-size: 15px; border-radius: 8px; }
            QPushButton:hover { background-color: #3A3636; color: white; }
        """)
        self.settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self.settings_btn)

    def _open_settings(self):
        popover = SettingsPopover(self.settings_store, self._logged_in, self)
        popover.quality_changed.connect(self.quality_changed_sig.emit)
        popover.stream_changed.connect(self.stream_changed_sig.emit)
        popover.login_requested.connect(self.login_requested.emit)
        popover.logout_requested.connect(self.logout_requested.emit)
        popover.adjustSize()
        top_left = self.mapToGlobal(self.settings_btn.pos())
        popover.move(top_left.x() - popover.width() + 60, top_left.y() - popover.height() - 8)
        popover.exec()

    def set_user(self, nickname: str, vip_type, avatar_url: str | None):
        """已登录：昵称 + VIP 标 + 异步加载的圆形头像。"""
        self._logged_in = True
        self.name_label.setText(nickname or "已登录")
        if vip_type == 110:
            self.vip_label.setText("黑胶 VIP")
            self.vip_label.show()
        elif vip_type:
            self.vip_label.setText("VIP")
            self.vip_label.show()
        else:
            self.vip_label.hide()
        if avatar_url:
            request = QNetworkRequest(QUrl(avatar_url))
            reply = self.nam.get(request)
            reply.finished.connect(lambda: self._on_avatar_loaded(reply))
        else:
            self._set_avatar_text((nickname or "客")[:1])

    def set_guest(self):
        self._logged_in = False
        self.name_label.setText("游客")
        self.vip_label.hide()
        self._set_avatar_text("客")

    def _set_avatar_text(self, text: str):
        self.avatar_label.setText(text)
        self.avatar_label.setPixmap(QPixmap())

    def _on_avatar_loaded(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = bytes(reply.readAll())
        finally:
            reply.deleteLater()
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.avatar_label.setText("")
            self.avatar_label.setPixmap(circle_pixmap(pixmap, 38))


# ---------------------------------------------------------------------------
# 左栏组合
# ---------------------------------------------------------------------------
class LeftPanel(QWidget):
    new_chat_requested = pyqtSignal()
    session_selected = pyqtSignal(str, str)
    session_delete_requested = pyqtSignal(str)
    login_requested = pyqtSignal()
    logout_requested = pyqtSignal()
    quality_changed = pyqtSignal(str)
    stream_changed = pyqtSignal(bool)

    def __init__(self, nam: QNetworkAccessManager, settings_store):
        super().__init__()
        self.setMinimumWidth(220)  # 交给 QSplitter 拖拽，默认宽度由 setSizes 给出（264）

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.new_chat_bar = NewChatBar()
        self.history_list = HistoryList()
        self.user_bar = UserBar(nam, settings_store)
        root.addWidget(self.new_chat_bar)
        root.addWidget(self.history_list, 1)
        root.addWidget(self.user_bar)

        self.new_chat_bar.new_chat_requested.connect(self.new_chat_requested.emit)
        self.history_list.session_selected.connect(self.session_selected.emit)
        self.history_list.delete_requested.connect(self.session_delete_requested.emit)
        self.user_bar.quality_changed_sig.connect(self.quality_changed.emit)
        self.user_bar.stream_changed_sig.connect(self.stream_changed.emit)
        self.user_bar.login_requested.connect(self.login_requested.emit)
        self.user_bar.logout_requested.connect(self.logout_requested.emit)

    def set_sessions(self, sessions: list):
        self.history_list.set_sessions(sessions)

    def set_active_session(self, session_id: str):
        self.history_list.set_active(session_id)

    def upsert_front(self, session_id: str, title: str):
        self.history_list.upsert_front(session_id, title)

    def remove_session(self, session_id: str):
        self.history_list.remove_session(session_id)

    def set_user(self, nickname: str, vip_type, avatar_url=None):
        self.user_bar.set_user(nickname, vip_type, avatar_url)

    def set_guest(self):
        self.user_bar.set_guest()
