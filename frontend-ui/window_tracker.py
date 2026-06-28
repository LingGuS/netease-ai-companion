import ctypes
from ctypes import wintypes
from PyQt6.QtCore import QTimer, QObject

# 获取 Windows 系统底层的 user32 动态链接库
user32 = ctypes.windll.user32

class WindowTracker(QObject):
    """
    Windows 窗口追踪器
    用于实时监听网易云音乐的位置，并将 AI 聊天窗口吸附在它右侧
    """
    def __init__(self, chat_window):
        super().__init__()
        self.chat_window = chat_window
        
        # 使用 PyQt 的定时器，保证与 GUI 运行在同一主线程，避免崩溃
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.track_and_snap)
        
        # 记录上一次的坐标，防止不必要的重复移动导致的画面抖动
        self.last_pos = None

    def start(self):
        """启动追踪，每 20 毫秒（约 50 FPS）侦测一次"""
        self.timer.start(20)

    def stop(self):
        """停止追踪"""
        self.timer.stop()

    def track_and_snap(self):
        """核心逻辑：寻找窗口并计算吸附位置"""
        
        # 1. 寻找网易云音乐窗口
        # 网易云 PC 端的底层窗口类名长期以来都是 "OrpheusBrowserHost"
        # 这种找法无视当前播放的歌曲名叫什么，非常稳定
        hwnd = user32.FindWindowW("OrpheusBrowserHost", None)
        
        # 如果通过类名没找到，作为备用方案，尝试寻找标题为“网易云音乐”的窗口
        if not hwnd:
            hwnd = user32.FindWindowW(None, "网易云音乐")

        # 2. 如果网易云根本没打开，或者被最小化到了托盘
        if not hwnd or user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
            # 隐藏我们的 AI 助手窗口
            if self.chat_window.isVisible():
                self.chat_window.hide()
            return

        # 3. 如果网易云正常显示，确保我们的窗口也被唤醒
        if not self.chat_window.isVisible():
            self.chat_window.show()

        # 4. 获取网易云窗口的实时坐标和大小
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        
        net_x = rect.left
        net_y = rect.top
        net_width = rect.right - rect.left
        net_height = rect.bottom - rect.top

        # 5. 计算吸附位置
        # 我们想把它放在网易云的右侧，并且顶部对齐
        # 这里的 offset_x 可以用来微调吸附的间距，设置为 0 就是严丝合缝地贴着
        offset_x = 0  
        target_x = net_x + net_width + offset_x
        target_y = net_y

        # 为了防止 AI 窗口的高度超过网易云的高度显得突兀，你可以取消下面这行的注释，让它们高度强行一致：
        # self.chat_window.setFixedHeight(net_height)

        # 6. 移动 AI 窗口
        if self.last_pos != (target_x, target_y):
            self.chat_window.move(target_x, target_y)
            self.last_pos = (target_x, target_y)
            
            # 确保 AI 窗口始终在最前（可选，如果网易云被其他窗口遮挡，AI窗口依然可见）
            # self.chat_window.raise_()