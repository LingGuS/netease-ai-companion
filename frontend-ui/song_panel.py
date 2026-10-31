"""歌曲容器（中栏）：圆形旋转黑胶封面 + 流动歌词 + 底部播放条 + 歌曲列表抽屉。

与设计稿（design/demo.html）对齐：
- 封面为圆形黑胶盘，播放时匀速旋转（24s/圈），暂停/停止即停转；
- 歌词解析 LRC 逐行高亮，随 QMediaPlayer 进度平滑滚动、上下渐隐；
- 底部播放条高度 76px（与左栏用户信息栏一致）：简要信息 + 进度 + 上一首/播放停止/下一首/列表；
- 「歌曲列表」点击后向上弹出抽屉，每行含封面/歌名/歌手/时长。
"""

import json
import re

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QRectF,
    QSize,
    Qt,
    QPropertyAnimation,
    QTimer,
    QUrl,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QPen,
    QRadialGradient,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from player import PlayerController
from ui_theme import SCROLLBAR_QSS

BACKEND = "http://127.0.0.1:8000"

# 封面未加载时的兜底渐变色（按 song_id 哈希取一）
_COVER_FALLBACKS = [
    ("#5B8DEF", "#2F4FA8"),
    ("#8F7BEF", "#46379E"),
    ("#4FC3A1", "#1E7A64"),
    ("#E0A458", "#8F5F26"),
    ("#EF6A8B", "#93294A"),
]

_LRC_TIME = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text: str) -> list:
    """解析 LRC 文本为 [(time_ms, line)]，按时间排序。"""
    lines = []
    for raw in (text or "").splitlines():
        stamps = list(_LRC_TIME.finditer(raw))
        if not stamps:
            continue
        content = _LRC_TIME.sub("", raw).strip()
        if not content:
            continue
        for m in stamps:
            minutes, seconds = int(m.group(1)), int(m.group(2))
            frac = (m.group(3) or "0").ljust(3, "0")[:3]
            lines.append(((minutes * 60 + seconds) * 1000 + int(frac), content))
    lines.sort(key=lambda x: x[0])
    return lines


def fmt_ms(ms: int) -> str:
    total = max(0, int(ms // 1000))
    return f"{total // 60}:{total % 60:02d}"


def rounded_pixmap(source: QPixmap, size: int, radius: int) -> QPixmap:
    """圆角方形封面。"""
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(
        0, 0,
        source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                      Qt.TransformationMode.SmoothTransformation),
    )
    painter.end()
    return out


# ---------------------------------------------------------------------------
# 圆形旋转黑胶封面
# ---------------------------------------------------------------------------
class DiscCover(QWidget):
    """圆形黑胶盘：外圈柔光 + 唱片纹路 + 中心轴孔；播放时旋转、停止即停转。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(306, 306)  # 含外圈柔光的绘制区域
        self._pixmap: QPixmap | None = None
        self._fallback = _COVER_FALLBACKS[0]
        self._angle = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)  # ~25fps
        self._timer.timeout.connect(self._tick)

    def set_playing(self, playing: bool):
        if playing:
            self._timer.start()
        else:
            self._timer.stop()

    def set_cover(self, pixmap: QPixmap | None):
        self._pixmap = pixmap
        self.update()

    def set_fallback(self, song_id):
        self._fallback = _COVER_FALLBACKS[hash(song_id) % len(_COVER_FALLBACKS)]
        self.update()

    def _tick(self):
        # 24s 一圈：40ms 转 360*0.04/24 度
        self._angle = (self._angle + 360 * 0.040 / 24.0) % 360.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w, h = self.width(), self.height()
        side = min(w, h) - 40  # 留出柔光带
        rect = QRectF((w - side) / 2, (h - side) / 2, side, side)
        center = rect.center()

        # 外圈柔光（不随盘旋转）
        glow = QRadialGradient(center, w / 2)
        glow_color = QColor(self._fallback[0])
        glow_color.setAlpha(70)
        glow.setColorAt(0.60, QColor(0, 0, 0, 0))
        glow.setColorAt(0.78, glow_color)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(self.rect(), QBrush(glow))

        # 唱片本体（随角度旋转）
        painter.save()
        clip = QPainterPath()
        clip.addEllipse(rect)
        painter.setClipPath(clip)
        painter.translate(center)
        painter.rotate(self._angle)
        painter.translate(-center)
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.drawPixmap(rect.toRect(), self._pixmap)
        else:
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0, QColor(self._fallback[0]))
            grad.setColorAt(1, QColor(self._fallback[1]))
            painter.fillRect(rect, QBrush(grad))
        # 纹路
        painter.setPen(QPen(QColor(255, 255, 255, 16), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for f in (0.30, 0.42, 0.54, 0.66, 0.78, 0.88):
            radius = side * f / 2
            painter.drawEllipse(center, radius, radius)
        painter.restore()

        # 中心轴孔（不随旋转）
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 16, 16, 235))
        painter.drawEllipse(center, 15, 15)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        painter.drawEllipse(center, 15, 15)
        painter.end()


# ---------------------------------------------------------------------------
# 流动歌词
# ---------------------------------------------------------------------------
class LyricsWidget(QWidget):
    """逐行高亮歌词：当前行居中白色，随播放进度平滑滚动，上下渐隐。"""

    LINE_H = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self._lines: list = []
        self._active = -1
        self._ease = 1.0        # 滚动缓动进度 0→1
        self._offset_lines = 0  # 缓动起始的行数差
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._empty_text = "♪ 暂无歌词"

    def _on_anim(self, value):
        self._ease = float(value)
        self.update()

    def set_empty_text(self, text: str):
        self._empty_text = text
        self.update()

    def set_lrc(self, lines: list):
        self._lines = lines or []
        self._active = -1
        self._ease = 1.0
        self._offset_lines = 0
        self.update()

    def set_position(self, ms: int):
        """播放进度驱动：高亮当前行并平滑滚动。"""
        if not self._lines:
            return
        active = -1
        for i, (t, _) in enumerate(self._lines):
            if ms >= t:
                active = i
        if active == self._active:
            return
        old = self._active
        self._active = active
        self._offset_lines = 0
        self._ease = 1.0
        # 顺序前进 1~2 行时平滑滚动；跳行（seek）直接吸附
        if old >= 0 and 0 < active - old <= 2:
            self._offset_lines = active - old
            self._ease = 0.0
            self._anim.stop()
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        if not self._lines:
            painter.setPen(QColor(131, 126, 126))
            font = QFont("Microsoft YaHei")
            font.setPixelSize(14)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            painter.end()
            return

        for i, (_, text) in enumerate(self._lines):
            rel = i - self._active
            y = h / 2 + rel * self.LINE_H + (1 - self._ease) * self._offset_lines * self.LINE_H
            if y < -self.LINE_H or y > h + self.LINE_H:
                continue
            font = QFont("Microsoft YaHei")
            if i == self._active:
                font.setPixelSize(17)
                font.setWeight(QFont.Weight.DemiBold)
                painter.setPen(QColor(255, 255, 255))
            else:
                dist = min(abs(rel), 3)
                font.setPixelSize(14)
                painter.setPen(QColor(131, 126, 126) if dist <= 1 else QColor(97, 93, 93))
            painter.setFont(font)
            metrics = QFontMetrics(font)
            elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(w) - 24)
            painter.drawText(
                QRectF(12, y - self.LINE_H / 2, w - 24, self.LINE_H),
                Qt.AlignmentFlag.AlignCenter, elided,
            )

        # 上下渐隐（用面板底色渐变盖在文字上）
        for top in (True, False):
            grad = QLinearGradient(0, 0 if top else h, 0, 44 if top else h - 44)
            if top:
                grad.setColorAt(0, QColor(43, 40, 40))
                grad.setColorAt(1, QColor(43, 40, 40, 0))
            else:
                grad.setColorAt(0, QColor(43, 40, 40, 0))
                grad.setColorAt(1, QColor(43, 40, 40))
            painter.fillRect(QRectF(0, 0 if top else h - 44, w, 44), QBrush(grad))
        painter.end()


# ---------------------------------------------------------------------------
# 歌曲列表抽屉（向上弹出）
# ---------------------------------------------------------------------------
class SongRow(QWidget):
    clicked = pyqtSignal(int)

    def __init__(self, index: int, name: str, artist: str, duration: str,
                 cover: QPixmap | None, is_current: bool):
        super().__init__()
        self.index = index
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 14, 8)
        layout.setSpacing(12)

        cover_label = QLabel()
        cover_label.setFixedSize(40, 40)
        cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if cover is not None:
            cover_label.setPixmap(rounded_pixmap(cover, 40, 8))
        else:
            cover_label.setText("♪")
            cover_label.setStyleSheet(
                "background-color: #454141; border-radius: 8px; color: #837E7E; font-size: 15px;"
            )
        layout.addWidget(cover_label)

        name_label = QLabel(("♪  " if is_current else "") + name)
        name_label.setStyleSheet(
            f"color: {'#F06060' if is_current else '#E2E2E2'}; font-size: 13px; background: transparent;"
        )
        artist_label = QLabel(artist)
        artist_label.setStyleSheet("color: #837E7E; font-size: 11.5px; background: transparent;")
        artist_label.setMaximumWidth(150)
        layout.addWidget(name_label, 1)
        layout.addWidget(artist_label, 0, Qt.AlignmentFlag.AlignRight)

        dur_label = QLabel(duration)
        dur_label.setStyleSheet("color: #777171; font-size: 12px; background: transparent;")
        layout.addWidget(dur_label)

        self.setStyleSheet("QWidget:hover { background-color: #3B3838; border-radius: 11px; }")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mouseReleaseEvent(event)


class SongListDrawer(QFrame):
    """歌曲列表抽屉：向上弹出，行 = 封面 / 歌名 / 歌手 / 时长。"""

    song_clicked = pyqtSignal(int)

    def __init__(self, nam: QNetworkAccessManager, parent=None):
        super().__init__(parent)
        self.nam = nam
        self.setFixedHeight(430)
        self.setStyleSheet("""
            QFrame {
                background-color: #322F2F;
                border-top-left-radius: 18px; border-top-right-radius: 18px;
                border-top: 1px solid rgba(255,255,255,0.08);
            }
        """ + SCROLLBAR_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setStyleSheet("background: transparent;")
        head_layout = QHBoxLayout(header)
        head_layout.setContentsMargins(18, 16, 12, 10)
        title = QLabel("当前播放列表")
        title.setStyleSheet("color: white; font-size: 14px; background: transparent;")
        self.count_label = QLabel("0 首")
        self.count_label.setStyleSheet("color: #A0A0A0; font-size: 11.5px; background: transparent;")
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.close_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #A0A0A0;
                          font-size: 14px; border-radius: 8px; }
            QPushButton:hover { background-color: #3A3636; color: white; }
        """)
        head_layout.addWidget(title)
        head_layout.addSpacing(8)
        head_layout.addWidget(self.count_label)
        head_layout.addStretch()
        head_layout.addWidget(self.close_btn)
        root.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.content = QWidget()
        self.content.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.content)
        self.list_layout.setContentsMargins(12, 0, 12, 14)
        self.list_layout.setSpacing(2)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

    def refresh(self, queue: list, current_index: int, cover_cache: dict):
        """按播放队列重建列表行。"""
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.count_label.setText(f"{len(queue)} 首")
        if not queue:
            empty = QLabel("还没有歌曲，让 AI 推荐一首吧～")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #837E7E; font-size: 13px; background: transparent; padding: 30px;")
            self.list_layout.insertWidget(0, empty)
            return
        for i, payload in enumerate(queue):
            artists = " / ".join(payload.get("artists") or []) or "未知歌手"
            duration = fmt_ms(payload.get("duration_ms") or 0)
            row = SongRow(i, payload.get("name", "未知"), artists, duration,
                          cover_cache.get(payload.get("cover_url")),
                          is_current=(i == current_index))
            row.clicked.connect(self.song_clicked.emit)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)


# ---------------------------------------------------------------------------
# 底部播放条
# ---------------------------------------------------------------------------
class SeekSlider(QSlider):
    """进度条：点击凹槽即跳转到该位置（Qt 默认只做翻页且不触发 sliderPressed，
    导致拖拽保护失效、值被 positionChanged 拽回）。统一经 seek_started/seek_finished
    通知 PlayerBar 挂起/恢复自动回写。"""

    seek_started = pyqtSignal()
    seek_finished = pyqtSignal(int)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(),
                int(event.position().x()), max(self.width() - 1, 1),
            )
            self.setValue(value)
            self.seek_started.emit()
            self.sliderMoved.emit(value)  # 时间标签立即跟随
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek_finished.emit(self.value())
        super().mouseReleaseEvent(event)


class PlayerBar(QFrame):
    prev_clicked = pyqtSignal()
    next_clicked = pyqtSignal()
    play_clicked = pyqtSignal()
    list_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFixedHeight(76)
        self._dragging_slider = False
        self._duration_ms = 0
        self._position_setter = None
        self._volume_setter = None
        self.setStyleSheet("""
            QFrame {
                background-color: #333030;
                border-top: 1px solid rgba(255,255,255,0.08);
            }
            QLabel { background: transparent; border: none; }
            QPushButton { background: transparent; border: none; color: #A0A0A0;
                          font-family: 'Segoe UI Symbol', 'Microsoft YaHei';
                          font-size: 13px; border-radius: 16px; }
            QPushButton:hover { color: white; background-color: #3F3B3B; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(14)

        self.cover_label = QLabel("♪")
        self.cover_label.setFixedSize(44, 44)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet(
            "QLabel { background-color: #454141; border-radius: 9px; color: #837E7E; font-size: 17px; }"
        )
        layout.addWidget(self.cover_label)

        meta = QVBoxLayout()
        meta.setSpacing(4)
        self.name_label = QLabel("未在播放")
        self.name_label.setTextFormat(Qt.TextFormat.RichText)
        self.name_label.setStyleSheet("color: white; font-size: 13px;")
        progress_row = QHBoxLayout()
        progress_row.setSpacing(9)
        self.slider = SeekSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimumWidth(120)
        self.slider.setRange(0, 0)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.12); border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #E04E4E; border-radius: 2px; }
            QSlider::handle:horizontal { width: 10px; height: 10px; margin: -3px 0;
                                         border-radius: 5px; background: #E04E4E; }
        """)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet("color: #777171; font-size: 10.5px;")
        progress_row.addWidget(self.slider)
        progress_row.addWidget(self.time_label)
        meta.addWidget(self.name_label)
        meta.addLayout(progress_row)
        layout.addLayout(meta, 1)

        self.prev_btn = QPushButton("❮❮")
        self.play_btn = QPushButton("▸")
        self.next_btn = QPushButton("❯❯")
        self.list_btn = QPushButton("☰")
        self.play_btn.setFixedSize(42, 42)
        self.play_btn.setStyleSheet("""
            QPushButton { background-color: #E04E4E; color: white; border: none;
                          border-radius: 21px; font-size: 17px;
                          font-family: 'Segoe UI Symbol', 'Microsoft YaHei'; }
            QPushButton:hover { background-color: #F06060; }
        """)
        for btn in (self.prev_btn, self.next_btn, self.list_btn):
            btn.setFixedSize(34, 34)
        self.prev_btn.clicked.connect(self.prev_clicked.emit)
        self.next_btn.clicked.connect(self.next_clicked.emit)
        self.play_btn.clicked.connect(self.play_clicked.emit)
        self.list_btn.clicked.connect(self.list_clicked.emit)
        layout.addWidget(self.prev_btn)
        layout.addWidget(self.play_btn)
        layout.addWidget(self.next_btn)
        divider = QFrame()
        divider.setFixedSize(1, 26)
        divider.setStyleSheet("background-color: rgba(255,255,255,0.1);")
        layout.addWidget(divider)
        layout.addWidget(self.list_btn)

        # 音量按钮：悬停向上弹出垂直滑块
        self.volume_btn = QPushButton()
        self.volume_btn.setFixedSize(34, 34)
        self.volume_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.volume_btn.setIconSize(QSize(15, 15))
        self.volume_btn.setToolTip("音量")
        layout.addWidget(self.volume_btn)
        self._build_volume_popup()
        self.volume_btn.installEventFilter(self)
        self.volume_popup.installEventFilter(self)

        self.slider.seek_started.connect(self._on_seek_started)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.seek_finished.connect(self._on_seek_finished)

    def _on_seek_started(self):
        self._dragging_slider = True

    def _on_seek_finished(self, value: int):
        self._dragging_slider = False
        if self._position_setter is not None:
            self._position_setter(value)

    def _on_slider_moved(self, value):
        if self._dragging_slider and self._duration_ms > 0:
            self.time_label.setText(f"{fmt_ms(value)} / {fmt_ms(self._duration_ms)}")

    # ------------------------------------------------------------------
    # 音量：悬停按钮向上弹出垂直滑块（自下到上）
    # ------------------------------------------------------------------
    def _build_volume_popup(self):
        self.volume_popup = QFrame(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.volume_popup.setFixedSize(38, 156)
        self.volume_popup.setStyleSheet("""
            QFrame { background-color: #322F2F;
                     border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; }
        """)
        pop_layout = QVBoxLayout(self.volume_popup)
        pop_layout.setContentsMargins(6, 10, 6, 10)
        self.volume_slider = QSlider(Qt.Orientation.Vertical)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(QSettings("netease-ai-companion", "frontend").value("volume", 80)))
        self.volume_slider.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.volume_slider.setStyleSheet("""
            QSlider::groove:vertical { width: 4px; background: rgba(255,255,255,0.12); border-radius: 2px; }
            QSlider::add-page:vertical { background: #E04E4E; border-radius: 2px; }
            QSlider::sub-page:vertical { background: rgba(255,255,255,0.12); }
            QSlider::handle:vertical { width: 12px; height: 12px; margin: -4px -4px;
                                       border-radius: 6px; background: #E04E4E; }
        """)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        pop_layout.addWidget(self.volume_slider, 0, Qt.AlignmentFlag.AlignHCenter)

    def _open_volume_popup(self):
        top_left = self.volume_btn.mapToGlobal(QPoint(0, 0))
        x = top_left.x() + self.volume_btn.width() // 2 - self.volume_popup.width() // 2
        y = top_left.y() - self.volume_popup.height() - 4
        self.volume_popup.move(x, y)
        self.volume_popup.show()
        self.volume_popup.raise_()

    def _maybe_hide_volume_popup(self):
        if not self.volume_btn.underMouse() and not self.volume_popup.underMouse():
            self.volume_popup.hide()

    def _on_volume_changed(self, value: int):
        if self._volume_setter is not None:
            self._volume_setter(value / 100)
        QSettings("netease-ai-companion", "frontend").setValue("volume", value)
        icon = (
            QStyle.StandardPixmap.SP_MediaVolumeMuted
            if value == 0
            else QStyle.StandardPixmap.SP_MediaVolume
        )
        self.volume_btn.setIcon(self.style().standardIcon(icon))

    def set_volume_setter(self, setter):
        self._volume_setter = setter
        self._on_volume_changed(self.volume_slider.value())  # 让持久化的音量立即生效

    def eventFilter(self, obj, event):
        if obj is self.volume_btn:
            if event.type() == QEvent.Type.Enter:
                self._open_volume_popup()
            elif event.type() == QEvent.Type.Leave:
                QTimer.singleShot(250, self._maybe_hide_volume_popup)
        elif obj is self.volume_popup and event.type() == QEvent.Type.Leave:
            QTimer.singleShot(250, self._maybe_hide_volume_popup)
        return super().eventFilter(obj, event)

    def set_position_setter(self, setter):
        self._position_setter = setter

    def set_now_playing(self, name: str, artist: str):
        self.name_label.setText(f"{name} <span style='color:#A0A0A0; font-size:11px;'>{artist}</span>")

    def set_mini_cover(self, pixmap: QPixmap | None):
        if pixmap is not None:
            self.cover_label.setText("")
            self.cover_label.setPixmap(rounded_pixmap(pixmap, 44, 9))
        else:
            self.cover_label.setText("♪")
            self.cover_label.setPixmap(QPixmap())

    def set_playing_icon(self, playing: bool):
        self.play_btn.setText("‖" if playing else "▸")

    def update_progress(self, position_ms: int, duration_ms: int):
        self._duration_ms = duration_ms
        if duration_ms > 0 and duration_ms != self.slider.maximum():
            self.slider.setRange(0, duration_ms)  # 量程不变时不重设，避免打断拖拽
        if not self._dragging_slider:
            self.slider.setValue(min(position_ms, duration_ms) if duration_ms > 0 else 0)
            self.time_label.setText(f"{fmt_ms(position_ms)} / {fmt_ms(duration_ms)}")


# ---------------------------------------------------------------------------
# 歌曲容器组合
# ---------------------------------------------------------------------------
class SongPanel(QWidget):
    """中栏：封面/歌词区 + 播放条 + 抽屉（覆盖层）。"""

    BAR_HEIGHT = 76

    def __init__(self, nam: QNetworkAccessManager):
        super().__init__()
        self.setMinimumWidth(520)  # 交给 QSplitter 拖拽，默认宽度由 setSizes 给出（700）
        self.nam = nam
        self.controller: PlayerController | None = None
        self._cover_cache: dict = {}
        self._lyric_song_id = None
        self._drawer_open = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        main_area = QWidget()
        main_area.setStyleSheet("background-color: #2B2828;")
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(30, 16, 30, 0)
        main_layout.setSpacing(0)

        disc_wrap = QHBoxLayout()
        disc_wrap.addStretch()
        self.disc = DiscCover()
        disc_wrap.addWidget(self.disc)
        disc_wrap.addStretch()
        main_layout.addLayout(disc_wrap)

        self.title_label = QLabel("未在播放")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet(
            "color: white; font-size: 23px; font-weight: 600; background: transparent;"
        )
        self.artist_label = QLabel("")
        self.artist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_label.setStyleSheet("color: #A0A0A0; font-size: 13px; background: transparent;")
        main_layout.addSpacing(10)
        main_layout.addWidget(self.title_label)
        main_layout.addSpacing(6)
        main_layout.addWidget(self.artist_label)

        self.lyrics = LyricsWidget()
        main_layout.addSpacing(8)
        main_layout.addWidget(self.lyrics, 1)
        root.addWidget(main_area, 1)

        self.player_bar = PlayerBar()
        root.addWidget(self.player_bar)

        # 覆盖层：遮罩 + 抽屉
        self.scrim = QWidget(self)
        self.scrim.setStyleSheet("background-color: rgba(0,0,0,0.35);")
        self.scrim.hide()
        self.drawer = SongListDrawer(nam, self)
        self.drawer.hide()
        self._drawer_anim = QPropertyAnimation(self.drawer, b"pos", self)
        self._drawer_anim.setDuration(280)
        self._drawer_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ------------------------------------------------------------------
    # 接线
    # ------------------------------------------------------------------
    def attach_controller(self, controller: PlayerController):
        self.controller = controller
        controller.current_changed.connect(self._on_current_changed)
        controller.queue_changed.connect(self._refresh_drawer)
        controller.stateChanged.connect(self._on_state_changed)
        controller.player.positionChanged.connect(self._on_position)
        controller.player.durationChanged.connect(self._on_duration)

        self.player_bar.prev_clicked.connect(controller.prev_track)
        self.player_bar.next_clicked.connect(controller.next_track)
        self.player_bar.play_clicked.connect(controller.toggle_play_pause)
        self.player_bar.list_clicked.connect(self.toggle_drawer)
        self.player_bar.set_position_setter(controller.player.setPosition)
        self.player_bar.set_volume_setter(controller.audio_output.setVolume)
        self.drawer.song_clicked.connect(controller.play_index)
        self.drawer.close_btn.clicked.connect(self.close_drawer)
        self.scrim.mousePressEvent = lambda _event: self.close_drawer()

    # ------------------------------------------------------------------
    # 当前歌曲变化
    # ------------------------------------------------------------------
    def _on_current_changed(self, payload: dict):
        name = payload.get("name", "未知")
        artists = " / ".join(payload.get("artists") or [])
        album = payload.get("album") or ""
        artist_text = f"{artists} · {album}" if album else artists
        self.title_label.setText(name)
        self.artist_label.setText(artist_text)
        self.player_bar.set_now_playing(name, artists)

        self.disc.set_cover(None)
        self.disc.set_fallback(payload.get("song_id"))
        self.player_bar.set_mini_cover(None)
        self._load_cover(payload.get("cover_url"))

        song_id = payload.get("song_id")
        if song_id != self._lyric_song_id:
            self._lyric_song_id = song_id
            self.lyrics.set_empty_text("♪ 歌词加载中…")
            self.lyrics.set_lrc([])
            self._load_lyric(song_id)
        self._refresh_drawer()

    def _on_state_changed(self, song_id: int, state: str):
        self.disc.set_playing(state == "playing")
        self.player_bar.set_playing_icon(state == "playing")

    def _on_position(self, ms: int):
        duration = self.controller.player.duration() if self.controller else 0
        self.player_bar.update_progress(ms, duration)
        self.lyrics.set_position(ms)

    def _on_duration(self, ms: int):
        position = self.controller.player.position() if self.controller else 0
        self.player_bar.update_progress(position, ms)

    # ------------------------------------------------------------------
    # 封面 / 歌词异步加载
    # ------------------------------------------------------------------
    def _load_cover(self, url):
        if not url:
            return
        if url in self._cover_cache:
            pixmap = self._cover_cache[url]
            self.disc.set_cover(pixmap)
            self.player_bar.set_mini_cover(pixmap)
            return
        request = QNetworkRequest(QUrl(url))
        reply = self.nam.get(request)
        reply.finished.connect(lambda: self._on_cover_loaded(reply, url))

    def _on_cover_loaded(self, reply, url):
        pixmap = None
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                pixmap = QPixmap()
                if not pixmap.loadFromData(bytes(reply.readAll())):
                    pixmap = None
        finally:
            reply.deleteLater()
        if pixmap is None:
            return
        self._cover_cache[url] = pixmap
        current = {}
        if self.controller is not None and 0 <= self.controller.current_index < len(self.controller.queue):
            current = self.controller.queue[self.controller.current_index]
        if current.get("cover_url") == url:
            self.disc.set_cover(pixmap)
            self.player_bar.set_mini_cover(pixmap)
        self._refresh_drawer()

    def _load_lyric(self, song_id):
        if not song_id:
            self.lyrics.set_empty_text("♪ 暂无歌词")
            return
        request = QNetworkRequest(QUrl(f"{BACKEND}/lyric/{song_id}"))
        reply = self.nam.get(request)
        reply.finished.connect(lambda: self._on_lyric_loaded(reply, song_id))

    def _on_lyric_loaded(self, reply, song_id):
        lrc_text = ""
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = json.loads(bytes(reply.readAll()).decode())
                lrc_text = data.get("lrc") or ""
        except (ValueError, UnicodeDecodeError):
            lrc_text = ""
        finally:
            reply.deleteLater()
        if song_id != self._lyric_song_id:
            return
        lines = parse_lrc(lrc_text)
        if lines:
            self.lyrics.set_lrc(lines)
        else:
            self.lyrics.set_empty_text("♪ 暂无歌词")

    # ------------------------------------------------------------------
    # 抽屉
    # ------------------------------------------------------------------
    def _refresh_drawer(self):
        if self.controller is not None:
            self.drawer.refresh(list(self.controller.queue), self.controller.current_index,
                                self._cover_cache)

    def _layout_overlays(self):
        w, h = self.width(), self.height()
        bar_h = self.BAR_HEIGHT
        self.scrim.setGeometry(0, 0, w, h - bar_h)
        drawer_h = self.drawer.height()
        if self._drawer_open:
            self.drawer.setGeometry(0, h - bar_h - drawer_h, w, drawer_h)
        else:
            self.drawer.setGeometry(0, h, w, drawer_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_overlays()

    def open_drawer(self):
        self._drawer_open = True
        self.scrim.raise_()
        self.drawer.raise_()
        self.scrim.show()
        self.drawer.show()
        self._layout_overlays()
        self._drawer_anim.stop()
        self._drawer_anim.setStartValue(QPoint(0, self.height()))
        self._drawer_anim.setEndValue(QPoint(0, self.height() - self.BAR_HEIGHT - self.drawer.height()))
        self._drawer_anim.start()

    def close_drawer(self):
        self._drawer_open = False
        self._layout_overlays()
        self._drawer_anim.stop()
        self._drawer_anim.setStartValue(self.drawer.pos())
        self._drawer_anim.setEndValue(QPoint(0, self.height()))
        self._drawer_anim.start()

    def toggle_drawer(self):
        if self._drawer_open:
            self.close_drawer()
        else:
            self.open_drawer()
