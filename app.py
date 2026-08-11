"""
WMS 立体库看板 - 多库区后端
支持 F区、H区 两个独立 SQL Server 数据库
提供总览页和分区详细页
"""
import os
import sys
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pyodbc
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS

import config

if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__,
            static_folder=os.path.join(base_dir, 'static'),
            static_url_path='/static')
CORS(app)

# =====================================================
# 利用率历史记录 (SQLite)
# =====================================================
HISTORY_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'history.db')
SNAPSHOT_INTERVAL_SEC = 3600  # 每小时采集一次


def _init_history_db():
    """初始化 SQLite 历史记录表"""
    conn = sqlite3.connect(HISTORY_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS utilization_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            f_occupied INTEGER NOT NULL,
            f_total INTEGER NOT NULL,
            f_pct REAL NOT NULL,
            h_occupied INTEGER NOT NULL,
            h_total INTEGER NOT NULL,
            h_pct REAL NOT NULL,
            combined_pct REAL NOT NULL,
            UNIQUE(recorded_at)
        )
    ''')
    conn.commit()
    conn.close()


def _record_utilization():
    """采集当前利用率快照并写入 SQLite"""
    if config.MOCK_MODE:
        return  # 模拟模式不采集
    try:
        now = datetime.now().replace(minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M')
        snapshots = {}
        for zone in ['f', 'h']:
            z = config.ZONES[zone]
            conn = get_db(zone)
            try:
                sql = """
                    SELECT
                        COUNT(*)                                                      AS total,
                        SUM(CASE WHEN KW_STATE = 4 THEN 1 ELSE 0 END)                 AS occupied
                    FROM WMS_HJ_KW
                """
                row = query_db(sql, conn=conn, one=True)
                total = _to_int(row['total']) if row else 0
                occupied = _to_int(row['occupied']) if row else 0
                pct = round(occupied / total * 100, 1) if total else 0
                snapshots[zone] = {'occupied': occupied, 'total': total, 'pct': pct}
            finally:
                conn.close()

        combined_pct = round(
            (snapshots['f']['occupied'] + snapshots['h']['occupied'])
            / (snapshots['f']['total'] + snapshots['h']['total']) * 100, 1
        ) if (snapshots['f']['total'] + snapshots['h']['total']) else 0

        db = sqlite3.connect(HISTORY_DB)
        db.execute(
            'INSERT OR REPLACE INTO utilization_history '
            '(recorded_at, f_occupied, f_total, f_pct, h_occupied, h_total, h_pct, combined_pct) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (now, snapshots['f']['occupied'], snapshots['f']['total'], snapshots['f']['pct'],
             snapshots['h']['occupied'], snapshots['h']['total'], snapshots['h']['pct'], combined_pct)
        )
        db.commit()
        db.close()
        print(f"[采集] {now} 利用率已记录 - F区:{snapshots['f']['pct']}% H区:{snapshots['h']['pct']}% 合计:{combined_pct}%")
    except Exception as e:
        print(f"[采集] 失败: {e}")


def _snapshot_loop():
    """后台线程：定时采集 + 清理过期数据"""
    while True:
        time.sleep(SNAPSHOT_INTERVAL_SEC)
        try:
            _record_utilization()
            # 清理 90 天前的旧数据
            cutoff = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d %H:%M')
            db = sqlite3.connect(HISTORY_DB)
            db.execute('DELETE FROM utilization_history WHERE recorded_at < ?', (cutoff,))
            db.commit()
            db.close()
        except Exception as e:
            print(f"[后台线程] 异常: {e}")


def _start_history_thread():
    """启动后台采集线程"""
    _init_history_db()
    if not config.MOCK_MODE:
        # 启动后立即采集一次
        time.sleep(10)  # 等 Flask 完全启动
        _record_utilization()
    t = threading.Thread(target=_snapshot_loop, daemon=True, name='util-snapshot')
    t.start()
    print("[历史记录] 后台采集线程已启动 (间隔: {}分钟)".format(SNAPSHOT_INTERVAL_SEC // 60))

# =====================================================
# 工具函数
# =====================================================
def _to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return default

STATE_NAMES = {0: '无效', 1: '禁用', 2: '入库锁定', 3: '出库锁定', 4: '有货', 5: '无货', 6: '空托'}

def get_state_name(v):
    s = _to_int(v)
    return STATE_NAMES.get(s, f'未知({s})')

def parse_layer_from_code(code):
    if code and len(str(code).strip()) >= 4:
        return _to_int(str(code).strip()[2:4])
    return 0

def _sql_nvarchar_literal(s):
    return "N'" + str(s).replace("'", "''") + "'"

def get_db(zone):
    conn_str = config.get_connection_string(zone)
    return pyodbc.connect(conn_str)

def query_db(sql, zone=None, conn=None, one=False):
    if conn:
        cursor = conn.cursor()
    else:
        db = get_db(zone)
        cursor = db.cursor()
    try:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        if one:
            row = cursor.fetchone()
            if row:
                return dict(zip(columns, row))
            return None
        rows = cursor.fetchall()
        return [dict(zip(columns, r)) for r in rows]
    finally:
        if not conn:
            db.close()

# =====================================================
# 模拟数据生成
# =====================================================
_mock_cache = {}


def _build_mock_data(zone):
    import random
    import uuid

    z = config.ZONES[zone]
    rows = z['total_rows']
    layers = z['total_layers']
    cols = z['total_cols']

    def rkzy_id():
        return str(uuid.uuid4()).replace('-', '')[:10].upper()

    products = [
        ('PS-ZN501', 'GG-200X150', 'XH-A100', 'TMGZ20250315001', '深圳电子科技'),
        ('SC-MC330', 'GG-300X200', 'XH-B200', 'TMGZ20250315002', '上海机电股份'),
        ('QG-ZD210', 'GG-180X120', 'XH-C150', 'TMGZ20250315003', '广州气动元件'),
        ('JQ-JS408', 'GG-250X250', 'XH-D300', 'TMGZ20250315004', '天津传动设备'),
        ('CL-BB902', 'GG-150X100', 'XH-E120', 'TMGZ20250315005', '武汉液压件'),
        ('TG-CC705', 'GG-400X300', 'XH-F400', 'TMGZ20250315006', '南京自动化'),
        ('XT-ZH106', 'GG-220X180', 'XH-G180', 'TMGZ20250315007', '成都精密机械'),
        ('BC-LL307', 'GG-350X280', 'XH-H350', 'TMGZ20250315008', '西安重工设备'),
        ('DJ-WW504', 'GG-160X140', 'XH-I160', 'TMGZ20250315009', '东莞微型电机'),
        ('ZK-QQ809', 'GG-280X260', 'XH-J280', 'TMGZ20250315010', '苏州真空科技'),
        ('FD-FF601', 'GG-320X240', 'XH-K320', 'TMGZ20250315011', '浙江阀门制造'),
        ('YJ-GG202', 'GG-190X170', 'XH-L190', 'TMGZ20250315012', '沈阳液压设备'),
        ('CZ-AA110', 'GG-210X190', 'XH-M210', 'TMGZ20250315013', '厦门传感器厂'),
        ('DJ-BB808', 'GG-270X230', 'XH-N270', 'TMGZ20250315014', '青岛驱动器厂'),
    ]

    def rand_date(days_back=365):
        return (datetime.now() - timedelta(days=random.randint(0, days_back))).strftime('%Y-%m-%d %H:%M:%S')

    def rand_older(days_min, days_max):
        return (datetime.now() - timedelta(days=random.randint(days_min, days_max))).strftime('%Y-%m-%d %H:%M:%S')

    all_locs = []
    for row in range(1, rows + 1):
        for layer in range(1, layers + 1):
            for col in range(1, cols + 1):
                code = f"{row:02d}{layer:02d}{col:02d}"
                loc = {
                    'locationCode': code,
                    'area': zone.upper(),
                    'row': row,
                    'layer': layer,
                    'col': col,
                    'state': 5,  # 无货
                    'product': None,
                }
                all_locs.append(loc)

    random.seed(42 + hash(zone) % 100)

    state_weights = [
        (4, 0.12),  # 有货 12%
        (5, 0.70),  # 无货 70%
        (6, 0.06),  # 空托 6%
        (2, 0.04),  # 入库锁定 4%
        (3, 0.03),  # 出库锁定 3%
        (1, 0.03),  # 禁用 3%
        (0, 0.02),  # 无效 2%
    ]
    states, weights = zip(*state_weights)
    total = sum(weights)
    probs = [w / total for w in weights]

    for loc in all_locs:
        loc['state'] = random.choices(states, weights=probs)[0]
        if loc['state'] == 4:
            p = random.choice(products)
            age_r = random.random()
            if age_r < 0.30:
                inbound = rand_older(0, 7)
            elif age_r < 0.60:
                inbound = rand_older(8, 30)
            elif age_r < 0.80:
                inbound = rand_older(31, 60)
            else:
                inbound = rand_older(61, 365)
            barcode_val = p[3]
            if random.random() < 0.3:
                extras = random.randint(1, 2)
                barcode_val = barcode_val + ',' + ','.join([f"TM{random.randint(100000,999999)}" for _ in range(extras)])
            roll_count = len([x for x in barcode_val.split(',') if x.strip()]) * 2
            loc['product'] = {
                'palletNumber': f"TP{random.randint(100000, 999999)}",
                'barcode': barcode_val,
                'materielCode': p[0],
                'spec': p[1],
                'model': p[2],
                'productTime': p[4],
                'rollCount': roll_count,
                'inboundTime': inbound,
            }

    grouped = {}
    for loc in all_locs:
        l = loc['layer']
        if l not in grouped:
            grouped[l] = []
        grouped[l].append(loc)

    return grouped


def get_mock_locs_for_layer(zone, layer):
    if not _mock_cache.get(zone):
        _mock_cache[zone] = _build_mock_data(zone)
    l = int(layer)
    return _mock_cache[zone].get(l, [])


def get_mock_all_locs(zone):
    if not _mock_cache.get(zone):
        _mock_cache[zone] = _build_mock_data(zone)
    return _mock_cache[zone]


# =====================================================
# 页面路由
# =====================================================
@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/f')
def page_f():
    return app.send_static_file('f.html')

@app.route('/h')
def page_h():
    return app.send_static_file('h.html')


# =====================================================
# API: 配置
# =====================================================
@app.route('/api/config')
def api_config():
    zone = request.args.get('zone', 'f')
    z = config.ZONES.get(zone, config.ZONES['f'])
    return jsonify({
        'zone': zone,
        'zoneName': z['name'],
        'totalRows': z['total_rows'],
        'totalLayers': z['total_layers'],
        'totalCols': z['total_cols'],
        'ageBuckets': config.DEFAULT_AGE_BUCKETS,
    })


# =====================================================
# API: 单层/全部 库位数据
# =====================================================
@app.route('/api/locations')
def api_locations():
    zone = request.args.get('zone', 'f')
    layer = request.args.get('layer', '')

    if config.MOCK_MODE:
        if layer == 'all':
            grouped = get_mock_all_locs(zone)
            return jsonify({str(k): grouped[k] for k in sorted(grouped.keys())})
        else:
            locs = get_mock_locs_for_layer(zone, layer)
            return jsonify(locs)

    z = config.ZONES[zone]
    layer_code = str(int(layer)).zfill(2)

    sql = f"""
        SELECT
            SUBSTRING(k.HJ_KW_CODE, 1, 2)  AS row_no,
            SUBSTRING(k.HJ_KW_CODE, 3, 2)  AS layer_no,
            SUBSTRING(k.HJ_KW_CODE, 5, 2)  AS col_no,
            k.HJ_KW_CODE                     AS location_code,
            k.KW_STATE                       AS state,
            r.PALLET_NUMBER,
            r.TM                             AS barcode,
            r.MATERIEL_CODE,
            r.GG                             AS spec,
            r.XH                             AS model,
            r.SJ                             AS product_time,
            r.RKSJ                           AS inbound_time
        FROM WMS_HJ_KW k
        LEFT JOIN WMS_RKZY r ON k.CURRENT_RKZY_ID = r.RKZY_ID
        WHERE SUBSTRING(k.HJ_KW_CODE, 3, 2) = ?
        ORDER BY SUBSTRING(k.HJ_KW_CODE, 1, 2), SUBSTRING(k.HJ_KW_CODE, 5, 2)
    """
    conn = get_db(zone)
    cursor = conn.cursor()
    cursor.execute(sql, (layer_code,))
    columns = [col[0] for col in cursor.description]
    rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
    conn.close()

    result = []
    for r in rows:
        loc = _build_loc_from_row(r, zone)
        loc['layer'] = _to_int(r.get('layer_no'))
        result.append(loc)
    return jsonify(result)


def _build_loc_from_row(r, zone):
    row_no = _to_int(r.get('row_no'))
    layer_no = _to_int(r.get('layer_no'))
    col_no = _to_int(r.get('col_no'))
    location_code = str(r.get('location_code', '') or '').strip()

    loc = {
        'locationCode': location_code,
        'area': zone.upper(),
        'row': row_no,
        'layer': layer_no,
        'col': col_no,
        'state': _to_int(r.get('state')),
        'product': None
    }
    if r.get('MATERIEL_CODE') or r.get('PALLET_NUMBER'):
        inbound_time = r.get('inbound_time')
        barcode_val = r.get('barcode') or ''
        roll_count = len([x for x in barcode_val.split(',') if x.strip()]) * 2
        loc['product'] = {
            'palletNumber': r.get('PALLET_NUMBER') or '',
            'barcode': barcode_val,
            'materielCode': r.get('MATERIEL_CODE') or '',
            'spec': r.get('spec') or '',
            'model': r.get('model') or '',
            'productTime': r.get('product_time') or '',
            'rollCount': roll_count,
            'inboundTime': inbound_time.strftime('%Y-%m-%d %H:%M:%S')
                if inbound_time and hasattr(inbound_time, 'strftime')
                else str(inbound_time or ''),
        }
    return loc


@app.route('/api/locations/all')
def api_locations_all():
    zone = request.args.get('zone', 'f')

    if config.MOCK_MODE:
        grouped = get_mock_all_locs(zone)
        return jsonify({str(k): grouped[k] for k in sorted(grouped.keys())})

    z = config.ZONES[zone]
    sql = """
        SELECT
            SUBSTRING(k.HJ_KW_CODE, 1, 2)  AS row_no,
            SUBSTRING(k.HJ_KW_CODE, 3, 2)  AS layer_no,
            SUBSTRING(k.HJ_KW_CODE, 5, 2)  AS col_no,
            k.HJ_KW_CODE                     AS location_code,
            k.KW_STATE                       AS state,
            r.PALLET_NUMBER,
            r.TM                             AS barcode,
            r.MATERIEL_CODE,
            r.GG                             AS spec,
            r.XH                             AS model,
            r.SJ                             AS product_time,
            r.RKSJ                           AS inbound_time
        FROM WMS_HJ_KW k
        LEFT JOIN WMS_RKZY r ON k.CURRENT_RKZY_ID = r.RKZY_ID
        ORDER BY SUBSTRING(k.HJ_KW_CODE, 3, 2),
                 SUBSTRING(k.HJ_KW_CODE, 1, 2),
                 SUBSTRING(k.HJ_KW_CODE, 5, 2)
    """
    conn = get_db(zone)
    cursor = conn.cursor()
    cursor.execute(sql)
    columns = [col[0] for col in cursor.description]
    rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
    conn.close()

    grouped = {}
    for r in rows:
        layer = _to_int(r.get('layer_no'))
        if layer not in grouped:
            grouped[layer] = []
        grouped[layer].append(_build_loc_from_row(r, zone))

    return jsonify({str(k): grouped[k] for k in sorted(grouped.keys())})


# =====================================================
# API: 仪表盘数据
# =====================================================
@app.route('/api/dashboard')
def api_dashboard():
    zone = request.args.get('zone', 'f')

    if config.MOCK_MODE:
        return jsonify(_mock_dashboard(zone))

    z = config.ZONES[zone]
    conn = get_db(zone)

    try:
        result = {'zone': zone, 'zoneName': z['name']}

        # --- 2.1 指标卡 ---
        metric_sql = """
            SELECT
                COUNT(*)                                                           AS total_locs,
                SUM(CASE WHEN KW_STATE = 4 THEN 1 ELSE 0 END)                      AS occupied,
                SUM(CASE WHEN KW_STATE = 5 THEN 1 ELSE 0 END)                      AS empty_locs,
                SUM(CASE WHEN KW_STATE IN (1, 2, 3) THEN 1 ELSE 0 END)              AS locked,
                SUM(CASE WHEN KW_STATE = 6 THEN 1 ELSE 0 END)                      AS empty_pallet,
                SUM(CASE WHEN KW_STATE = 0 THEN 1 ELSE 0 END)                      AS invalid_cnt,
                COUNT(DISTINCT CASE WHEN KW_STATE = 4 THEN r.MATERIEL_CODE END)    AS product_types,
                COUNT(DISTINCT CASE WHEN KW_STATE = 4 THEN r.PALLET_NUMBER END)    AS pallet_count
            FROM WMS_HJ_KW k
            LEFT JOIN WMS_RKZY r ON k.CURRENT_RKZY_ID = r.RKZY_ID
        """
        m = query_db(metric_sql, conn=conn, one=True)
        total = _to_int(m['total_locs']) if m else 0
        occupied = _to_int(m['occupied']) if m else 0
        result['metrics'] = {
            'total': total,
            'occupied': occupied,
            'empty': _to_int(m['empty_locs']) if m else 0,
            'locked': _to_int(m['locked']) if m else 0,
            'emptyPallet': _to_int(m['empty_pallet']) if m else 0,
            'invalid': _to_int(m['invalid_cnt']) if m else 0,
            'productTypes': _to_int(m['product_types']) if m else 0,
            'palletCount': _to_int(m['pallet_count']) if m else 0,
            'occupiedPct': round(occupied / total * 100, 1) if total else 0,
        }

        # --- 2.2 状态分布 ---
        status_sql = """
            SELECT KW_STATE, COUNT(*) AS cnt
            FROM WMS_HJ_KW
            GROUP BY KW_STATE
            ORDER BY KW_STATE
        """
        status_rows = query_db(status_sql, conn=conn)
        result['charts'] = {}
        result['charts']['status'] = [
            {'name': get_state_name(r['KW_STATE']), 'state': _to_int(r['KW_STATE']), 'value': r['cnt']}
            for r in status_rows
        ]

        # --- 2.3 库龄分布 ---
        buckets = config.DEFAULT_AGE_BUCKETS
        age_label_cases = []
        age_order_cases = []
        for idx, b in enumerate(buckets):
            if b['maxDays'] is not None:
                cond = f"DATEDIFF(DAY, r.RKSJ, GETDATE()) <= {int(b['maxDays'])}"
                age_label_cases.append(f"WHEN {cond} THEN {_sql_nvarchar_literal(b['label'])}")
                age_order_cases.append(f"WHEN {cond} THEN {idx}")
            else:
                age_label_cases.append(f"ELSE {_sql_nvarchar_literal(b['label'])}")
                age_order_cases.append(f"ELSE {idx}")
        age_label_expr = 'CASE ' + ' '.join(age_label_cases) + ' END'
        age_order_expr = 'CASE ' + ' '.join(age_order_cases) + ' END'
        age_sql = 'SELECT ' + age_label_expr + """ AS age_bucket,
                """ + age_order_expr + """ AS bucket_order,
                COUNT(*) AS cnt
            FROM WMS_RKZY r
            INNER JOIN WMS_HJ_KW k ON r.RKZY_ID = k.CURRENT_RKZY_ID
            WHERE k.KW_STATE = 4 AND r.RKSJ IS NOT NULL
            GROUP BY """ + age_label_expr + ', ' + age_order_expr + """
            ORDER BY bucket_order"""
        age_rows = query_db(age_sql, conn=conn)
        result['charts']['age'] = [{'name': r['age_bucket'], 'value': r['cnt']} for r in age_rows]

        # --- 2.4 各层有货率 ---
        layer_sql = """
            SELECT
                SUBSTRING(HJ_KW_CODE, 3, 2) AS layer_no,
                COUNT(*) AS total,
                SUM(CASE WHEN KW_STATE = 4 THEN 1 ELSE 0 END) AS occupied
            FROM WMS_HJ_KW
            WHERE KW_STATE <> 0
            GROUP BY SUBSTRING(HJ_KW_CODE, 3, 2)
            ORDER BY layer_no
        """
        layer_rows = query_db(layer_sql, conn=conn)
        layer_map = {_to_int(r['layer_no']): r for r in layer_rows}
        result['charts']['layer'] = []
        for ln in range(1, z['total_layers'] + 1):
            r = layer_map.get(ln)
            total_ln = _to_int(r['total']) if r else 0
            occ_ln = _to_int(r['occupied']) if r else 0
            result['charts']['layer'].append({
                'layer': 'L' + str(ln),
                'pct': round(occ_ln / total_ln * 100, 1) if total_ln else 0
            })

        # --- 2.5 产品型号分布 ---
        spec_sql = """
            SELECT TOP 20
                r.XH AS spec,
                COUNT(*) AS cnt
            FROM WMS_RKZY r
            INNER JOIN WMS_HJ_KW k ON r.RKZY_ID = k.CURRENT_RKZY_ID
            WHERE k.KW_STATE = 4 AND r.XH IS NOT NULL
            GROUP BY r.XH
            ORDER BY cnt DESC
        """
        spec_rows = query_db(spec_sql, conn=conn)
        result['charts']['spec'] = [{'spec': r['spec'], 'count': r['cnt']} for r in spec_rows]

        # --- 2.6 近 30 天入库趋势 ---
        trend_sql = """
            SELECT
                CONVERT(VARCHAR(10), RKSJ, 120) AS inbound_date,
                COUNT(*) AS cnt
            FROM WMS_RKZY
            WHERE RKSJ >= DATEADD(DAY, -29, CAST(GETDATE() AS DATE))
              AND RKSJ IS NOT NULL
            GROUP BY CONVERT(VARCHAR(10), RKSJ, 120)
            ORDER BY inbound_date
        """
        trend_rows = query_db(trend_sql, conn=conn)
        trend_map = {r['inbound_date']: r['cnt'] for r in trend_rows}
        result['charts']['trend'] = []
        for i in range(30):
            d = datetime.now().date() - timedelta(days=29 - i)
            date_str = d.strftime('%Y-%m-%d')
            result['charts']['trend'].append({
                'date': d.strftime('%m/%d'),
                'count': _to_int(trend_map.get(date_str, 0))
            })

        return jsonify(result)
    finally:
        conn.close()


def _mock_dashboard(zone):
    z = config.ZONES[zone]
    import random
    random.seed(42 + hash(zone) % 100)

    total = z['total_rows'] * z['total_layers'] * z['total_cols']
    occupied = int(total * 0.12)
    empty = int(total * 0.70)
    locked = int(total * 0.07)
    empty_pallet = int(total * 0.06)
    invalid_cnt = int(total * 0.02)
    disabled = total - occupied - empty - locked - empty_pallet - invalid_cnt

    return {
        'zone': zone,
        'zoneName': z['name'],
        'metrics': {
            'total': total,
            'occupied': occupied,
            'empty': empty,
            'locked': locked,
            'emptyPallet': empty_pallet,
            'invalid': invalid_cnt,
            'productTypes': 14,
            'palletCount': occupied,
            'occupiedPct': round(occupied / total * 100, 1),
        },
        'charts': {
            'status': [
                {'name': '无效', 'state': 0, 'value': invalid_cnt},
                {'name': '禁用', 'state': 1, 'value': disabled},
                {'name': '入库锁定', 'state': 2, 'value': int(locked * 0.57)},
                {'name': '出库锁定', 'state': 3, 'value': int(locked * 0.43)},
                {'name': '有货', 'state': 4, 'value': occupied},
                {'name': '无货', 'state': 5, 'value': empty},
                {'name': '空托', 'state': 6, 'value': empty_pallet},
            ],
            'age': [
                {'name': '≤7天', 'value': int(occupied * 0.30)},
                {'name': '8-30天', 'value': int(occupied * 0.30)},
                {'name': '31-60天', 'value': int(occupied * 0.20)},
                {'name': '>60天', 'value': int(occupied * 0.20)},
            ],
            'layer': [{'layer': f'L{i}', 'pct': round(random.uniform(8, 20), 1)} for i in range(1, z['total_layers'] + 1)],
            'spec': [{'spec': f'XH-{chr(65+i)}', 'count': random.randint(10, 80)} for i in range(14)],
            'trend': [],
        }
    }
    # Generate mock trend data
    for i in range(30):
        d = datetime.now().date() - timedelta(days=29 - i)
        result['charts']['trend'].append({
            'date': d.strftime('%m/%d'),
            'count': random.randint(30, 200),
        })
    return result


# =====================================================
# API: 总览（合并两个库区）
# =====================================================
@app.route('/api/overview')
def api_overview():
    results = {}
    for zone in ['f', 'h']:
        if config.MOCK_MODE:
            results[zone] = _mock_dashboard(zone)
        else:
            z = config.ZONES[zone]
            conn = get_db(zone)
            try:
                metric_sql = """
                    SELECT
                        COUNT(*)                                                           AS total_locs,
                        SUM(CASE WHEN KW_STATE = 4 THEN 1 ELSE 0 END)                      AS occupied,
                        SUM(CASE WHEN KW_STATE = 5 THEN 1 ELSE 0 END)                      AS empty_locs,
                        SUM(CASE WHEN KW_STATE IN (1, 2, 3) THEN 1 ELSE 0 END)              AS locked,
                        SUM(CASE WHEN KW_STATE = 6 THEN 1 ELSE 0 END)                      AS empty_pallet,
                        COUNT(DISTINCT CASE WHEN KW_STATE = 4 THEN r.MATERIEL_CODE END)    AS product_types,
                        COUNT(DISTINCT CASE WHEN KW_STATE = 4 THEN r.PALLET_NUMBER END)    AS pallet_count
                    FROM WMS_HJ_KW k
                    LEFT JOIN WMS_RKZY r ON k.CURRENT_RKZY_ID = r.RKZY_ID
                """
                m = query_db(metric_sql, conn=conn, one=True)
                total = _to_int(m['total_locs']) if m else 0
                occupied = _to_int(m['occupied']) if m else 0

                # Status distribution
                status_sql = """
                    SELECT KW_STATE, COUNT(*) AS cnt
                    FROM WMS_HJ_KW
                    GROUP BY KW_STATE
                    ORDER BY KW_STATE
                """
                status_rows = query_db(status_sql, conn=conn)

                # Trend
                trend_sql = """
                    SELECT
                        CONVERT(VARCHAR(10), RKSJ, 120) AS inbound_date,
                        COUNT(*) AS cnt
                    FROM WMS_RKZY
                    WHERE RKSJ >= DATEADD(DAY, -29, CAST(GETDATE() AS DATE))
                      AND RKSJ IS NOT NULL
                    GROUP BY CONVERT(VARCHAR(10), RKSJ, 120)
                    ORDER BY inbound_date
                """
                trend_rows = query_db(trend_sql, conn=conn)
                trend_map = {r['inbound_date']: r['cnt'] for r in trend_rows}
                trend_list = []
                for i in range(30):
                    d = datetime.now().date() - timedelta(days=29 - i)
                    date_str = d.strftime('%Y-%m-%d')
                    trend_list.append({
                        'date': d.strftime('%m/%d'),
                        'count': _to_int(trend_map.get(date_str, 0))
                    })

                results[zone] = {
                    'zone': zone,
                    'zoneName': z['name'],
                    'metrics': {
                        'total': total,
                        'occupied': occupied,
                        'empty': _to_int(m['empty_locs']) if m else 0,
                        'locked': _to_int(m['locked']) if m else 0,
                        'emptyPallet': _to_int(m['empty_pallet']) if m else 0,
                        'productTypes': _to_int(m['product_types']) if m else 0,
                        'palletCount': _to_int(m['pallet_count']) if m else 0,
                        'occupiedPct': round(occupied / total * 100, 1) if total else 0,
                    },
                    'charts': {
                        'status': [
                            {'name': get_state_name(r['KW_STATE']), 'state': _to_int(r['KW_STATE']), 'value': r['cnt']}
                            for r in status_rows
                        ],
                        'trend': trend_list,
                    }
                }
            finally:
                conn.close()

    # Combine metrics
    f_metrics = results.get('f', {}).get('metrics', {})
    h_metrics = results.get('h', {}).get('metrics', {})
    combined = {
        'total': f_metrics.get('total', 0) + h_metrics.get('total', 0),
        'occupied': f_metrics.get('occupied', 0) + h_metrics.get('occupied', 0),
        'empty': f_metrics.get('empty', 0) + h_metrics.get('empty', 0),
        'locked': f_metrics.get('locked', 0) + h_metrics.get('locked', 0),
        'emptyPallet': f_metrics.get('emptyPallet', 0) + h_metrics.get('emptyPallet', 0),
        'productTypes': f_metrics.get('productTypes', 0) + h_metrics.get('productTypes', 0),
        'palletCount': f_metrics.get('palletCount', 0) + h_metrics.get('palletCount', 0),
    }
    combined['occupiedPct'] = round(combined['occupied'] / combined['total'] * 100, 1) if combined['total'] else 0

    return jsonify({
        'zones': results,
        'combined': combined,
    })


# =====================================================
# API: 单库位详情
# =====================================================
@app.route('/api/location/<code>')
def api_location_detail(code):
    zone = request.args.get('zone', 'f')

    if config.MOCK_MODE:
        all_locs = get_mock_all_locs(zone)
        for layer_locs in all_locs.values():
            for loc in layer_locs:
                if loc['locationCode'] == code:
                    return jsonify(loc)
        return jsonify({'error': '库位不存在'}), 404

    sql = """
        SELECT
            k.HJ_KW_CODE,
            k.KW_STATE,
            k.NOTE,
            r.PALLET_NUMBER,
            r.MATERIEL_CODE,
            r.GG AS spec,
            r.XH AS model,
            r.TM AS barcode,
            r.SJ AS product_time,
            r.REAL_HEIGHT,
            r.CJSJ AS create_time,
            r.RKSJ AS inbound_time,
            DATEDIFF(DAY, r.RKSJ, GETDATE()) AS storage_days
        FROM WMS_HJ_KW k
        LEFT JOIN WMS_RKZY r ON k.CURRENT_RKZY_ID = r.RKZY_ID
        WHERE k.HJ_KW_CODE = ?
    """
    conn = get_db(zone)
    cursor = conn.cursor()
    cursor.execute(sql, (code,))
    columns = [col[0] for col in cursor.description]
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({'error': '库位不存在'}), 404

    r = dict(zip(columns, row))
    loc_code = str(r.get('HJ_KW_CODE', '') or '').strip()
    inbound_time = r.get('inbound_time')
    barcode_val = r.get('barcode') or ''
    roll_count = len([x for x in barcode_val.split(',') if x.strip()]) * 2

    return jsonify({
        'locationCode': loc_code,
        'area': zone.upper(),
        'row': _to_int(loc_code[:2]) if len(loc_code) >= 6 else 0,
        'layer': parse_layer_from_code(loc_code),
        'col': _to_int(loc_code[4:6]) if len(loc_code) >= 6 else 0,
        'state': _to_int(r.get('KW_STATE')),
        'stateName': get_state_name(r.get('KW_STATE')),
        'note': r.get('NOTE', ''),
        'storageDays': r.get('storage_days', 0),
        'product': {
            'palletNumber': r.get('PALLET_NUMBER') or '',
            'materielCode': r.get('MATERIEL_CODE') or '',
            'spec': r.get('spec') or '',
            'model': r.get('model') or '',
            'barcode': barcode_val,
            'productTime': r.get('product_time') or '',
            'rollCount': roll_count,
            'realHeight': r.get('REAL_HEIGHT', 0),
            'createTime': r.get('create_time').strftime('%Y-%m-%d %H:%M:%S')
                if r.get('create_time') and hasattr(r.get('create_time'), 'strftime')
                else str(r.get('create_time') or ''),
            'inboundTime': inbound_time.strftime('%Y-%m-%d %H:%M:%S')
                if inbound_time and hasattr(inbound_time, 'strftime')
                else str(inbound_time or ''),
        } if (r.get('MATERIEL_CODE') or r.get('PALLET_NUMBER')) else None
    })


# =====================================================
# API: 搜索
# =====================================================
@app.route('/api/search')
def api_search():
    zone = request.args.get('zone', 'f')
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])

    if config.MOCK_MODE:
        all_locs = []
        grouped = get_mock_all_locs(zone)
        for layer_locs in grouped.values():
            all_locs.extend(layer_locs)
        kw = q.lower()
        results = []
        for loc in all_locs:
            p = loc.get('product')
            if not p:
                continue
            if (kw in (p.get('materielCode') or '').lower()
                    or kw in (p.get('barcode') or '').lower()
                    or kw in (p.get('spec') or '').lower()
                    or kw in (loc.get('locationCode') or '').lower()):
                results.append(loc)
        return jsonify(results[:30])

    sql = """
        SELECT TOP 30
            k.HJ_KW_CODE,
            k.KW_STATE,
            r.MATERIEL_CODE,
            r.TM AS barcode,
            r.GG AS spec,
            r.XH AS model,
            r.RKSJ AS inbound_time
        FROM WMS_HJ_KW k
        LEFT JOIN WMS_RKZY r ON k.CURRENT_RKZY_ID = r.RKZY_ID
        WHERE (
            r.MATERIEL_CODE LIKE ?
            OR r.TM LIKE ?
            OR r.GG LIKE ?
            OR k.HJ_KW_CODE LIKE ?
        )
        AND k.KW_STATE = 4
    """
    like = f'%{q}%'
    conn = get_db(zone)
    cursor = conn.cursor()
    cursor.execute(sql, (like, like, like, like))
    columns = [col[0] for col in cursor.description]
    rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
    conn.close()

    results = []
    for r in rows:
        code = str(r.get('HJ_KW_CODE', '') or '').strip()
        inbound_time = r.get('inbound_time')
        barcode_val = r.get('barcode') or ''
        roll_count = len([x for x in barcode_val.split(',') if x.strip()]) * 2
        results.append({
            'locationCode': code,
            'area': zone.upper(),
            'row': _to_int(code[:2]) if len(code) >= 6 else 0,
            'layer': parse_layer_from_code(code),
            'col': _to_int(code[4:6]) if len(code) >= 6 else 0,
            'state': _to_int(r.get('KW_STATE')),
            'product': {
                'materielCode': r.get('MATERIEL_CODE') or '',
                'barcode': barcode_val,
                'spec': r.get('spec') or '',
                'model': r.get('model') or '',
                'rollCount': roll_count,
                'inboundTime': inbound_time.strftime('%Y-%m-%d %H:%M:%S')
                    if inbound_time and hasattr(inbound_time, 'strftime')
                    else str(inbound_time or ''),
            }
        })
    return jsonify(results)


# =====================================================
# API: 利用率历史趋势
# =====================================================
@app.route('/api/utilization-history')
def api_utilization_history():
    days = request.args.get('days', '30')
    try:
        days = int(days)
    except ValueError:
        days = 30
    days = max(1, min(days, 90))  # 限制 1-90 天

    if config.MOCK_MODE:
        # 模拟模式：生成假历史数据（每天一条，带轻微上升趋势）
        import random
        random.seed(42)
        result = []
        base = 55
        for i in range(days):
            d = datetime.now().date() - timedelta(days=days - 1 - i)
            base += random.uniform(-0.5, 1.0)  # 每日波动，整体缓慢上升
            result.append({
                'date': d.strftime('%m/%d'),
                'f_pct': round(base + random.uniform(-2, 2), 1),
                'h_pct': round(base + random.uniform(-4, 3), 1),
                'combined_pct': round(base + random.uniform(-3, 2), 1),
            })
        return jsonify({'data': result, 'mode': 'mock'})

    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    try:
        db = sqlite3.connect(HISTORY_DB)
        # 按天聚合：取当天峰值利用率（MAX = 当日最大库存 ÷ 库容）
        rows = db.execute(
            'SELECT substr(recorded_at, 1, 10) AS date, '
            'ROUND(MAX(f_pct), 1), ROUND(MAX(h_pct), 1), ROUND(MAX(combined_pct), 1) '
            'FROM utilization_history '
            'WHERE recorded_at >= ? '
            'GROUP BY date ORDER BY date ASC',
            (cutoff,)
        ).fetchall()
        db.close()
        result = [
            {
                'date': r[0][5:] if len(r[0]) >= 5 else r[0],  # MM-DD 格式
                'f_pct': round(r[1], 1),
                'h_pct': round(r[2], 1),
                'combined_pct': round(r[3], 1),
            }
            for r in rows
        ]
        return jsonify({'data': result, 'mode': 'real'})
    except Exception as e:
        return jsonify({'data': [], 'mode': 'real', 'error': str(e)})


# =====================================================
# 启动
# =====================================================
def main():
    debug = '--debug' in sys.argv
    port = int(os.environ.get('PORT', 5000))
    print(f"WMS 多库区看板启动: http://localhost:{port}")
    print(f"  - 总览页: http://localhost:{port}/")
    print(f"  - F区:    http://localhost:{port}/f")
    print(f"  - H区:    http://localhost:{port}/h")
    print(f"  - 模式:   {'模拟数据' if config.MOCK_MODE else '真实数据库'}")
    if not config.MOCK_MODE:
        print(f"  - F区:    {config.ZONES['f']['db_server']}")
        print(f"  - H区:    {config.ZONES['h']['db_server']}")
    _start_history_thread()
    app.run(host='0.0.0.0', port=port, debug=debug)


# =====================================================
# 自动启动后台采集线程（支持 python app.py 和 gunicorn）
# =====================================================
_history_started = False


def _auto_start_history():
    global _history_started
    if _history_started:
        return
    _history_started = True
    _start_history_thread()


if __name__ == '__main__':
    main()
else:
    # gunicorn 加载时会执行此处，多个 worker 之间用 UNIQUE(recorded_at) 防重复
    _auto_start_history()
