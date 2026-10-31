"""前端离屏冒烟测试：不启动真实窗口，验证装配、模式切换、卡片驱动、抽屉与歌词。

用法:
    venv/Scripts/python.exe scripts/ui_smoke.py

覆盖点：
1. 主窗口三栏装配与默认纯对话态（带头像）
2. done 卡片 → 播放态切换（无头像）+ 队列入队 + 中栏歌曲信息
3. 歌曲列表抽屉开合、队列切换（下一首环绕）
4. LRC 解析与歌词行高亮定位
5. 新建对话生成新会话号
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend-ui"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from main_window import MODE_CHAT, MODE_NOW, MainWindow  # noqa: E402
from song_panel import parse_lrc  # noqa: E402


def main():
    app = QApplication(sys.argv)
    window = MainWindow()

    # 1) 默认纯对话态；窗口 = 屏幕可用区域 60%（最小尺寸兜底）；三栏为可拖拽 splitter
    from PyQt6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen().availableGeometry()
    expected_w = max(int(screen.width() * 0.6), MainWindow.MIN_WIDTH)
    expected_h = max(int(screen.height() * 0.6), MainWindow.MIN_HEIGHT)
    assert (window.width(), window.height()) == (expected_w, expected_h), (
        window.width(), window.height(), expected_w, expected_h)
    assert window.mode == MODE_CHAT
    assert window.middle_holder.isHidden()
    assert window.chat_panel._avatars_visible is True
    assert window.left_panel.minimumWidth() == 220
    assert window.song_panel.minimumWidth() == 520
    assert window.splitter.widget(2) is window.chat_panel
    assert window.splitter.handleWidth() == 4
    assert window.splitter.childrenCollapsible() is False

    # 2) 对话响应携带音乐卡 → 播放态
    payload = {
        "song_id": 186016, "name": "晴天", "artists": ["周杰伦"], "album": "叶惠美",
        "duration_ms": 269000, "cover_url": None, "playable": False, "play_url": None,
    }
    window._on_response_finished("为你选了周杰伦的《晴天》", [{"type": "music", "payload": payload}])
    assert window.mode == MODE_NOW
    assert not window.middle_holder.isHidden()
    assert window.chat_panel._avatars_visible is False
    assert len(window.player.queue) == 1
    assert window.song_panel.title_label.text() == "晴天"
    assert "周杰伦" in window.song_panel.artist_label.text()

    # 3) 抽屉开合 + 队列切换
    window.song_panel.open_drawer()
    assert window.song_panel._drawer_open
    window.song_panel.close_drawer()
    assert not window.song_panel._drawer_open

    second = dict(payload, song_id=186017, name="雨爱")
    index = window.player.append(second)
    assert len(window.player.queue) == 2
    window.player.play_index(index)
    assert window.song_panel.title_label.text() == "雨爱"
    window.player.next_track()
    assert window.player.current_index == 0  # 环绕回第一首

    # 4) LRC 解析与歌词定位
    lines = parse_lrc("[ti:元数据]\n[00:01.50]第一行\n[01:02]第二行")
    assert [t for t, _ in lines] == [1500, 62000], lines
    assert [s for _, s in lines] == ["第一行", "第二行"]
    window.song_panel.lyrics.set_lrc(lines)
    window.song_panel.lyrics.set_position(63000)
    assert window.song_panel.lyrics._active == 1

    # 5) player_control 卡驱动播放器；♪ 开关展开/收起；播放类指令自动拉开面板
    window.player.play_index(0)
    window._on_response_finished("好的", [{"type": "player_control", "payload": {"action": "next"}}])
    assert window.player.current_index == 1
    assert window.song_panel.title_label.text() == "雨爱"
    assert window.mode == MODE_NOW  # 播放态下保持
    window.set_mode(MODE_CHAT)
    assert window.chat_panel.panel_btn.isChecked() is False
    window._on_response_finished("好的", [{"type": "player_control", "payload": {"action": "pause"}}])
    assert window.mode == MODE_CHAT  # 暂停/停止不打扰当前界面
    window._on_response_finished("好的", [{"type": "player_control", "payload": {"action": "play"}}])
    assert window.mode == MODE_NOW  # 继续播放 → 面板自动拉开
    assert window.chat_panel.panel_btn.isChecked() is True
    window._toggle_panel()
    assert window.mode == MODE_CHAT  # ♪ 开关收起
    window.chat_panel.panel_btn.click()
    assert window.mode == MODE_NOW  # ♪ 开关展开（信号路径）
    window._on_response_finished("好的", [{"type": "player_control", "payload": {"action": "unknown"}}])  # 非法指令不崩

    # 5.5) 音量滑块联动 + 进度条 seek 事件链
    bar = window.song_panel.player_bar
    bar.volume_slider.setValue(30)
    assert abs(window.player.audio_output.volume() - 0.3) < 0.01
    bar.volume_slider.setValue(0)
    assert abs(window.player.audio_output.volume()) < 0.01  # 静音图标分支不崩
    bar.slider.setRange(0, 269000)
    bar.slider.setValue(100000)
    bar.slider.seek_finished.emit(150000)  # 点击跳转 → setPosition（离屏下不崩溃即可）
    assert bar._dragging_slider is False
    bar.slider.seek_started.emit()
    assert bar._dragging_slider is True

    # 5.6) 会话队列恢复（restore：只填充不自动播放）
    restore_payloads = [
        dict(payload, song_id=999001, name="恢复A"),
        dict(payload, song_id=999002, name="恢复B"),
    ]
    window.player.restore(restore_payloads, 1)
    assert window.player.current_index == 1
    assert len(window.player.queue) == 2
    assert window.song_panel.title_label.text() == "恢复B"
    assert window.player.queue[1].get("playable") is False  # 恢复态不携带 URL
    window.player.restore([], -1)
    assert window.player.queue == [] and window.player.current_index == -1

    # 5.7) 历史记录悬停删除（本地移除；按钮悬停显隐）
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QEnterEvent
    window.left_panel.set_sessions([
        {"session_id": "del-a", "title": "甲", "last_active_at": None},
        {"session_id": "del-b", "title": "乙", "last_active_at": None},
    ])
    rows = window.left_panel.history_list._rows
    assert len(rows) == 2 and rows[0].delete_btn.isHidden()
    enter_ev = QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1))
    rows[0].enterEvent(enter_ev)
    assert not rows[0].delete_btn.isHidden()
    rows[0].leaveEvent(QEvent(QEvent.Type.Leave))
    assert rows[0].delete_btn.isHidden()
    window.left_panel.remove_session("del-a")
    assert [r.title for r in window.left_panel.history_list._rows] == ["乙"]

    # 6) 回纯对话 + 新建对话
    window.set_mode(MODE_CHAT)
    assert window.chat_panel._avatars_visible is True
    old_session = window.session_id
    window._new_chat()
    assert window.session_id != old_session
    assert window.chat_panel.session_id == window.session_id
    assert window.mode == MODE_CHAT

    print("UI SMOKE PASS")


if __name__ == "__main__":
    main()
