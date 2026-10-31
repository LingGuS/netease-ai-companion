"""公共样式常量：完整化的 QScrollBar 样式。

Qt 滚动条若只样式化 handle/groove，sub-line/add-line/sub-page/add-page 仍是
原生渲染，深色界面下会出现白黑相间的马赛克块——这里整体接管。
"""

SCROLLBAR_QSS = """
QScrollBar:vertical { border: none; background: transparent; width: 6px; margin: 0; }
QScrollBar::handle:vertical { background-color: #555; min-height: 20px; border-radius: 3px; }
QScrollBar::handle:vertical:hover { background-color: #6E6868; }
QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {
    height: 0; width: 0; background: none; border: none; }
QScrollBar::sub-page:vertical, QScrollBar::add-page:vertical {
    background: none; border: none; }
QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
    background: none; border: none; width: 0; height: 0; }
"""
