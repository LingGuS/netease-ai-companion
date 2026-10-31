"""全局播放控制器：进程唯一 QMediaPlayer + 播放队列管理。

播放队列来自对话中收到的音乐卡片（cards payload），「上一首/下一首/列表点播」
在队列内切换；play_url 为 20 分钟时效签名链接，缺失或失效时经 POST /play/url
直取（不经过 LLM）。开播时向后端上报播放行为（play_events 落库，画像信号）。
"""

import json

from PyQt6.QtCore import QObject, QSettings, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

BACKEND = "http://127.0.0.1:8000"


class PlayerController(QObject):
    stateChanged = pyqtSignal(int, str)  # (song_id, "playing"/"paused"/"stopped")
    current_changed = pyqtSignal(dict)   # 当前曲目 payload 变化（含切歌/URL 刷新）
    queue_changed = pyqtSignal()

    def __init__(self, parent=None, nam: QNetworkAccessManager | None = None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.8)
        self.current_id = None
        self.nam = nam
        self.queue: list[dict] = []      # 播放队列：卡片 payload 列表
        self.current_index = -1
        self._url_refreshing = False     # URL 过期重取中（防循环重试）
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.errorOccurred.connect(self._on_media_error)

    # ------------------------------------------------------------------
    # 状态广播
    # ------------------------------------------------------------------
    def _on_state_changed(self, state):
        if self.current_id is None:
            return
        name = {
            QMediaPlayer.PlaybackState.PlayingState: "playing",
            QMediaPlayer.PlaybackState.PausedState: "paused",
        }.get(state, "stopped")
        self.stateChanged.emit(self.current_id, name)
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._report_play(self.current_id)

    def _on_media_error(self, error, error_string):
        """播放链接过期（20 分钟时效）等媒体错误：对当前曲目重取一次 URL 重播。"""
        if self.current_index < 0 or self._url_refreshing:
            return
        self.play_index(self.current_index, force_refresh=True)

    # ------------------------------------------------------------------
    # 队列管理
    # ------------------------------------------------------------------
    def append(self, payload: dict) -> int:
        """入队一首歌；同曲已在队列则原位刷新载荷，返回其下标。"""
        for i, p in enumerate(self.queue):
            if p.get("song_id") == payload.get("song_id"):
                self.queue[i] = payload
                self.queue_changed.emit()
                return i
        self.queue.append(payload)
        self.queue_changed.emit()
        return len(self.queue) - 1

    def play_index(self, index: int, force_refresh: bool = False) -> None:
        """播放队列中指定曲目；URL 缺失/强制刷新时经 /play/url 直取。"""
        if not (0 <= index < len(self.queue)):
            return
        self.current_index = index
        payload = dict(self.queue[index])
        if force_refresh:
            payload.pop("play_url", None)
        self.current_id = payload.get("song_id")
        self.current_changed.emit(payload)

        if payload.get("playable") and payload.get("play_url"):
            self._url_refreshing = False
            self.player.setSource(QUrl(payload["play_url"]))
            self.player.play()
        else:
            self._fetch_url(payload)

    def restore(self, payloads: list, current_index: int):
        """恢复某会话的播放队列（只填充不自动播放；play_url 播放时经 /play/url 实时取）。"""
        self.queue = list(payloads)
        self.current_index = current_index if 0 <= current_index < len(self.queue) else -1
        self.current_id = (
            self.queue[self.current_index].get("song_id") if self.current_index >= 0 else None
        )
        self.queue_changed.emit()
        if self.current_index >= 0:
            self.current_changed.emit(dict(self.queue[self.current_index]))

    def next_track(self) -> None:
        if self.queue:
            self.play_index((self.current_index + 1) % len(self.queue))

    def prev_track(self) -> None:
        if self.queue:
            self.play_index((self.current_index - 1) % len(self.queue))

    def stop(self) -> None:
        self.player.stop()

    def toggle_play_pause(self) -> None:
        """播放条主按钮：播放/暂停切换；停止态则重播当前曲目。"""
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        elif self.current_index >= 0:
            self.play_index(self.current_index)

    # ------------------------------------------------------------------
    # 播放链接直取与行为上报
    # ------------------------------------------------------------------
    def _fetch_url(self, payload: dict) -> None:
        """play_url 缺失/过期：POST /play/url 重取（游客可能仍不可播，静默降级）。"""
        if not self.nam:
            return
        self._url_refreshing = True
        settings = QSettings("netease-ai-companion", "frontend")
        level = settings.value("quality", "exhigh")
        request = QNetworkRequest(QUrl(f"{BACKEND}/play/url"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        body = json.dumps({"song_id": payload.get("song_id"), "level": level}).encode("utf-8")
        reply = self.nam.post(request, body)
        reply.finished.connect(lambda: self._on_url_reply(reply))

    def _on_url_reply(self, reply):
        try:
            self._url_refreshing = False
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = json.loads(bytes(reply.readAll()).decode())
        except (ValueError, UnicodeDecodeError):
            return
        finally:
            reply.deleteLater()

        if self.current_index < 0 or data.get("song_id") != self.current_id:
            return
        if data.get("playable") and data.get("play_url"):
            self.queue[self.current_index] = data
            self.queue_changed.emit()
            self.current_changed.emit(dict(data))
            self.player.setSource(QUrl(data["play_url"]))
            self.player.play()

    def handle_control(self, action: str):
        """执行 AI 播放控制指令（player_control 卡片；队列外指令静默忽略）。"""
        action = (action or "").strip().lower()
        state = self.player.playbackState()
        if action == "pause":
            if state == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
        elif action == "play":
            if state == QMediaPlayer.PlaybackState.PausedState:
                self.player.play()
            elif state == QMediaPlayer.PlaybackState.StoppedState and self.current_index >= 0:
                self.play_index(self.current_index)
        elif action == "next":
            self.next_track()
        elif action == "prev":
            self.prev_track()
        elif action == "stop":
            self.player.stop()

    def _report_play(self, song_id: int):
        """开播上报：play_events 落库（后端归因当前登录用户，失败静默）。"""
        if not self.nam:
            return
        request = QNetworkRequest(QUrl(f"{BACKEND}/play/event"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        body = json.dumps({"song_id": song_id}).encode("utf-8")
        self.nam.post(request, body)

    # ------------------------------------------------------------------
    # 兼容旧卡片组件（music_card.py，已不在新 UI 中使用）
    # ------------------------------------------------------------------
    def toggle(self, payload: dict):
        if not payload.get("playable") or not payload.get("play_url"):
            return
        song_id = payload.get("song_id")
        state = self.player.playbackState()
        if song_id == self.current_id and state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        if song_id == self.current_id and state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
            return
        index = self.append(payload)
        self.play_index(index)
