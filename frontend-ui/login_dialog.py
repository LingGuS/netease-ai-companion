"""扫码登录弹窗：居中显示二维码，2 秒轮询扫码状态（自 chat_widget 迁移）。"""

import base64
import json

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QCursor, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

BACKEND = "http://127.0.0.1:8000"


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
        self.qr_label.setStyleSheet("background-color: white; border-radius: 8px; color: #666;")
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
