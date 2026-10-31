import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

# 导入重构后的主窗口（三栏 + 双状态机）
from main_window import MainWindow


def main():
    """主程序入口：独立窗口应用（自绘标题栏，不再吸附网易云桌面端）。"""
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))

    # 主窗口：1618×916，无边框 + 自绘标题栏（见 main_window.TitleBar）
    window = MainWindow()
    window.setWindowFlags(Qt.WindowType.FramelessWindowHint)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
