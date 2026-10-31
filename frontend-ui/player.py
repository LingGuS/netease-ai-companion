"""全局播放控制器：进程唯一 QMediaPlayer，所有音乐卡片共享。

同时只播一首歌：同曲点击切播放/暂停，异曲点击换源重播；
状态变化经 stateChanged 信号广播，各卡片自行更新按钮图标。
开播时向后端上报播放行为（play_events 落库，画像/推荐信号）。
"""

import json

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

BACKEND = "http://127.0.0.1:8000"


class PlayerController(QObject):
    stateChanged = pyqtSignal(int, str)  # (song_id, "playing"/"paused"/"stopped")

    def __init__(self, parent=None, nam: QNetworkAccessManager | None = None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.8)
        self.current_id = None
        self.nam = nam
        self.player.playbackStateChanged.connect(self._on_state_changed)

    def _on_state_changed(self, state):
        if self.current_id is None:
            return
        name = {
            QMediaPlayer.PlaybackState.PlayingState: "playing",
            QMediaPlayer.PlaybackState.PausedState: "paused",
        }.get(state, "stopped")
        self.stateChanged.emit(self.current_id, name)

    def toggle(self, payload: dict):
        """卡片按钮点击：同曲切换播放/暂停，异曲换源重播。"""
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
        self.current_id = song_id
        self.player.setSource(QUrl(payload["play_url"]))
        self.player.play()
        self._report_play(song_id)

    def _report_play(self, song_id: int):
        """开播上报：play_events 落库（后端归因当前登录用户，失败静默）。"""
        if not self.nam:
            return
        request = QNetworkRequest(QUrl(f"{BACKEND}/play/event"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        body = json.dumps({"song_id": song_id}).encode("utf-8")
        self.nam.post(request, body)
