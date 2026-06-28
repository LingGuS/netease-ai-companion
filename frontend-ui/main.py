import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

# 导入我们上一步写好的聊天界面
from chat_widget import ChatWidget

# TODO: 后续导入窗口追踪器
from window_tracker import WindowTracker 

def main():
    """
    主程序入口函数
    """
    # 1. 初始化 PyQt 应用程序实例
    # sys.argv 允许程序从命令行接收参数
    app = QApplication(sys.argv)

    # 2. 实例化核心聊天窗口
    chat_window = ChatWidget()
    
    # 隐藏系统默认的标题栏（如果需要彻底做成无边框悬浮窗，请取消注释下一行）
    chat_window.setWindowFlags(Qt.WindowType.FramelessWindowHint)

    # 3. TODO: 启动网易云窗口吸附追踪器
    # 我们将在下一步编写 WindowTracker，它会在后台运行并不断调整 chat_window 的坐标
    tracker = WindowTracker(chat_window)
    tracker.start()

    # 4. 显示窗口
    chat_window.show()

    # 5. 进入应用的主事件循环，保持程序运行直到用户关闭窗口
    sys.exit(app.exec())

if __name__ == '__main__':
    # 确保直接运行此脚本时触发主函数
    main()