"""
WMS 立体库看板启动器（多库区）
双击 start.bat 启动，或 python start.py
"""
import os
import webbrowser

if __name__ == '__main__':
    if 'WMS_MOCK_MODE' not in os.environ:
        os.environ['WMS_MOCK_MODE'] = '0'  # 默认真实数据库，允许 .bat 覆盖
    from app import main
    import threading
    threading.Timer(1.5, lambda: webbrowser.open('http://localhost:5000')).start()
    main()
