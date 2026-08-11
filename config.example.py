"""
WMS 立体库看板 - 多库区配置（示例文件）
使用时复制为 config.py 并填入真实信息
"""

import os

# =====================================================
# 运行模式：由环境变量 WMS_MOCK_MODE 控制，默认 0（真实数据库）
# =====================================================
MOCK_MODE = os.environ.get('WMS_MOCK_MODE', '0') == '1'

# =====================================================
# 库龄分段默认值（前端可自定义覆盖）
# =====================================================
DEFAULT_AGE_BUCKETS = [
    {'label': '≤7天',   'maxDays': 7,   'color': '#10b981'},
    {'label': '8-30天',  'maxDays': 30,  'color': '#3b82f6'},
    {'label': '31-60天', 'maxDays': 60,  'color': '#eab308'},
    {'label': '>60天',   'maxDays': None,'color': '#ef4444'},
]

# =====================================================
# 库区配置
# =====================================================
ZONES = {
    'f': {
        'name': 'F区立库',
        'db_server': os.environ.get('WMS_F_DB_SERVER', '192.168.x.x'),
        'db_port':   int(os.environ.get('WMS_F_DB_PORT', '1433')),
        'db_name':   os.environ.get('WMS_F_DB_NAME', 'your_db'),
        'db_user':   os.environ.get('WMS_F_DB_USER', 'your_user'),
        'db_pass':   os.environ.get('WMS_F_DB_PASS', 'your_password'),
        'total_rows': 11,
        'total_layers': 8,
        'total_cols': 39,
    },
    'h': {
        'name': 'H区立库',
        'db_server': os.environ.get('WMS_H_DB_SERVER', '192.168.x.x'),
        'db_port':   int(os.environ.get('WMS_H_DB_PORT', '1433')),
        'db_name':   os.environ.get('WMS_H_DB_NAME', 'your_db'),
        'db_user':   os.environ.get('WMS_H_DB_USER', 'your_user'),
        'db_pass':   os.environ.get('WMS_H_DB_PASS', 'your_password'),
        'total_rows': 11,
        'total_layers': 9,
        'total_cols': 39,
    },
}


def get_connection_string(zone: str) -> str:
    """根据库区生成 pyodbc 连接字符串"""
    z = ZONES[zone]
    server = f"{z['db_server']},{z['db_port']}"
    db = z['db_name']
    user = z['db_user']
    pwd = z['db_pass']

    # 自动检测可用 ODBC 驱动
    drivers = [
        'ODBC Driver 18 for SQL Server',
        'ODBC Driver 17 for SQL Server',
        'ODBC Driver 13 for SQL Server',
        'ODBC Driver 11 for SQL Server',
        'SQL Server',
    ]
    import pyodbc
    available = [d for d in drivers if d in pyodbc.drivers()]
    driver = available[0] if available else drivers[-1]

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={db};"
        f"UID={user};"
        f"PWD={pwd};"
        f"TrustServerCertificate=yes;"
        f"Encrypt=no;"
        f"autocommit=True;"
        f"timeout=10;"
    )
    return conn_str
