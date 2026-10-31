"""音乐播放卡片：左侧歌名+歌手小字，右侧封面+居中播放/暂停按钮，赞/踩反馈。

播放控制与封面网络加载均依赖外部注入的共享实例（PlayerController
与 QNetworkAccessManager），由 ChatWidget 统一持有。
"""

import json

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

BACKEND = "http://127.0.0.1:8000"

_BTN_DEFAULT = """
    QPushButton {
        background-color: transparent; color: #8A8580; border: none;
        border-radius: 4px; font-size: 11px;
    }
    QPushButton:hover { color: white; background-color: rgba(255,255,255,30); }
"""
_BTN_LIKE_ON = """
    QPushButton {
        background-color: transparent; color: #E05A7A; border: none;
        border-radius: 4px; font-size: 11px;
    }
"""
_BTN_DISLIKE_ON = """
    QPushButton {
        background-color: transparent; color: #6A9BE0; border: none;
        border-radius: 4px; font-size: 11px;
    }
"""


class MusicCardWidget(QWidget):
    def __init__(self, payload: dict, controller, nam: QNetworkAccessManager, parent=None):
        super().__init__(parent)
        self.payload = payload
        self.controller = controller
        self.nam = nam
        self.feedback_state = None  # None / "like" / "dislike"

        self.setFixedHeight(76)
        self.setMaximumWidth(320)
        self.setStyleSheet(
            "MusicCardWidget { background-color: #383535; border-radius: 10px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        # ---- 左侧：歌名 + 歌手小字 + 可选试听提示 ----
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name_label = QLabel(payload.get("name") or "未知歌曲")
        name_label.setWordWrap(True)
        name_label.setStyleSheet(
            "color: white; font-size: 14px; font-weight: bold; border: none; background: transparent;"
        )
        artists = "/".join(a for a in (payload.get("artists") or []) if a) or "未知歌手"
        artist_label = QLabel(artists)
        artist_label.setStyleSheet(
            "color: #A0A0A0; font-size: 11px; border: none; background: transparent;"
        )
        text_col.addWidget(name_label)
        text_col.addWidget(artist_label)

        trial = payload.get("trial_info")
        if trial:
            try:
                seconds = max(0, int(trial.get("end", 0)) - int(trial.get("start", 0)))
                trial_label = QLabel(f"试听 {seconds} 秒")
                trial_label.setStyleSheet(
                    "color: #D0A050; font-size: 10px; border: none; background: transparent;"
                )
                text_col.addWidget(trial_label)
            except (TypeError, ValueError):
                pass

        # 赞 / 踩 反馈按钮（切换式：再点同按钮取消）
        feedback_row = QHBoxLayout()
        feedback_row.setSpacing(4)
        self.like_btn = QPushButton("♥ 赞")
        self.dislike_btn = QPushButton("✕ 踩")
        for b in (self.like_btn, self.dislike_btn):
            b.setFixedHeight(18)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_BTN_DEFAULT)
        self.like_btn.clicked.connect(lambda: self._send_feedback("like"))
        self.dislike_btn.clicked.connect(lambda: self._send_feedback("dislike"))
        feedback_row.addWidget(self.like_btn)
        feedback_row.addWidget(self.dislike_btn)
        feedback_row.addStretch()
        text_col.addLayout(feedback_row)
        layout.addLayout(text_col, 1)

        # ---- 右侧：封面 + 居中覆盖的播放/暂停按钮 ----
        self.cover = QLabel()
        self.cover.setFixedSize(56, 56)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setStyleSheet(
            "background-color: #55504E; border-radius: 6px; border: none;"
        )
        layout.addWidget(self.cover)

        self.play_btn = QPushButton(self.cover)
        self.play_btn.setFixedSize(26, 26)
        self.play_btn.move((56 - 26) // 2, (56 - 26) // 2)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(20, 18, 18, 170);
                color: white; border: none; border-radius: 13px; font-size: 11px;
            }
            QPushButton:hover { background-color: rgba(20, 18, 18, 210); }
            QPushButton:disabled { color: #8A8580; }
            """
        )
        self.play_btn.clicked.connect(lambda: self.controller.toggle(self.payload))
        if not payload.get("playable"):
            self.play_btn.setEnabled(False)
            self.play_btn.setToolTip(payload.get("message") or "登录后可播放")

        self.controller.stateChanged.connect(self._on_state_changed)
        self._update_icon("stopped")
        self._load_cover(payload.get("cover_url"))

    # ---- 内部逻辑 ----

    def _update_icon(self, state: str):
        if not self.payload.get("playable"):
            self.play_btn.setText("▷")
            return
        playing_this = (
            state == "playing" and self.payload.get("song_id") == self.controller.current_id
        )
        self.play_btn.setText("⏸" if playing_this else "▶")

    def _on_state_changed(self, song_id: int, state: str):
        if song_id == self.payload.get("song_id"):
            self._update_icon(state)

    def _load_cover(self, url: str):
        if not url:
            return
        # NetEase CDN 支持 ?param=WxH 裁剪参数
        request = QNetworkRequest(QUrl(f"{url}?param=200y200"))
        reply = self.nam.get(request)
        reply.finished.connect(lambda: self._on_cover_loaded(reply))

    def _on_cover_loaded(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return  # 加载失败保留占位色块
            pixmap = QPixmap()
            if pixmap.loadFromData(bytes(reply.readAll())):
                self.cover.setPixmap(
                    pixmap.scaled(
                        56,
                        56,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        finally:
            reply.deleteLater()

    # ---- 赞 / 踩 反馈 ----

    def _send_feedback(self, action: str):
        request = QNetworkRequest(QUrl(f"{BACKEND}/feedback"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        body = json.dumps({"song_id": self.payload.get("song_id"), "action": action}).encode("utf-8")
        reply = self.nam.post(request, body)
        reply.finished.connect(lambda: self._on_feedback_reply(reply, action))

    def _on_feedback_reply(self, reply, action: str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = json.loads(bytes(reply.readAll()).decode())
        except (ValueError, UnicodeDecodeError):
            return
        finally:
            reply.deleteLater()

        if not data.get("ok"):
            self.setToolTip(data.get("message") or "登录后可记录偏好")
            return
        result = data.get("result")
        if result == "canceled":
            self.feedback_state = None
        elif result == "liked":
            self.feedback_state = "like"
        elif result == "disliked":
            self.feedback_state = "dislike"
        self._update_feedback_style()

    def _update_feedback_style(self):
        self.like_btn.setStyleSheet(_BTN_LIKE_ON if self.feedback_state == "like" else _BTN_DEFAULT)
        self.dislike_btn.setStyleSheet(
            _BTN_DISLIKE_ON if self.feedback_state == "dislike" else _BTN_DEFAULT
        )
