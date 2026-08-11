# WMS 立体库看板 — Linux 服务器部署交接文档

---

## 一、项目概述

| 项目 | 说明 |
|------|------|
| **名称** | WMS 立体库看板（仓库管理系统 3D 可视化看板） |
| **功能** | 监控 F区 和 H区 两个独立库区的立体仓库实时状态，提供总览仪表盘、网格可视化详情、搜索等功能 |
| **技术栈** | Python Flask 3.1.3 + pyodbc (SQL Server) + ECharts + 原生 HTML/CSS/JS |
| **当前开发环境** | Windows，Python 3.9 |

---

## 二、部署文件清单（Linux 需要的最小文件集）

以下文件/目录需要部署到 Linux 服务器：

```
WMS/
├── app.py              # Flask 主应用（所有路由和业务逻辑）
├── config.py           # 数据库配置（F区/H区连接信息、库龄分段）
├── start.py            # 启动入口
├── requirements.txt    # Python 依赖
├── static/
│   ├── index.html      # 总览仪表盘页
│   ├── f.html          # F区网格可视化详情页
│   ├── h.html          # H区网格可视化详情页
│   └── echarts.min.js  # ECharts 图表库
```

**不需要部署的文件：**
- `dist/`、`build/` — 这是 Windows PyInstaller 打包产物（.exe/.pyd/.dll），Linux 上无法使用
- `*.bat` — Windows 批处理脚本
- `*.spec` — PyInstaller 打包配置

---

## 三、服务器环境要求

### 3.1 操作系统
- CentOS 7/8、Ubuntu 18.04+、Debian 10+ 等主流 Linux 发行版

### 3.2 Python 环境
- Python >= 3.9（推荐 3.9~3.12）

### 3.3 必备依赖

| 依赖 | 版本 | 说明 |
|------|------|------|
| Flask | 3.1.3 | Web 框架 |
| pyodbc | 5.3.0 | SQL Server 数据库驱动 |
| flask-cors | 6.0.5 | 跨域支持 |
| gunicorn | 最新 | Linux 生产级 WSGI 服务器（推荐） |

### 3.4 系统级依赖

pyodbc 在 Linux 上需要安装 ODBC 驱动才能连接 SQL Server：

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y unixodbc unixodbc-dev curl gnupg2

# 安装 Microsoft ODBC Driver 18 for SQL Server
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18

# CentOS/RHEL 7/8
sudo yum install -y unixODBC unixODBC-devel
curl https://packages.microsoft.com/config/rhel/8/prod.repo | sudo tee /etc/yum.repos.d/mssql-release.repo
sudo ACCEPT_EULA=Y yum install -y msodbcsql18
```

安装完成后验证 ODBC 驱动是否可用：

```bash
odbcinst -q -d
# 应看到: [ODBC Driver 18 for SQL Server]
```

---

## 四、数据库网络要求

应用需要连接两个 SQL Server 数据库：

| 库区 | 服务器地址 | 端口 | 数据库名 | 用户名 |
|------|-----------|------|----------|--------|
| **F区** | `192.168.11.166` | 1433 | `FST_WMS` | `FstWmsReport` |
| **H区** | `192.168.60.62` | 1433 | `FST_WMS` | `FstWmsReport` |

**防火墙要求：** 确保 Linux 服务器能访问上述两个 IP 的 1433 端口。

验证命令：

```bash
telnet 192.168.11.166 1433
telnet 192.168.60.62 1433
# 或
nc -zv 192.168.11.166 1433
nc -zv 192.168.60.62 1433
```

---

## 五、部署步骤

### 步骤 1：上传项目文件

将上述"部署文件清单"中的内容上传到服务器，例如：

```bash
mkdir -p /opt/wms
# 上传文件到 /opt/wms/
```

### 步骤 2：创建虚拟环境并安装依赖

```bash
cd /opt/wms

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装 Python 依赖
pip install -r requirements.txt
pip install gunicorn
```

### 步骤 3：配置数据库连接

数据库连接信息在 `config.py` 中，**支持通过环境变量覆盖**，无需修改代码。

```bash
# 方式一：设置环境变量（推荐，部署到不同环境时无需改代码）
export WMS_F_DB_SERVER="192.168.11.166"
export WMS_F_DB_PORT="1433"
export WMS_F_DB_NAME="FST_WMS"
export WMS_F_DB_USER="FstWmsReport"
export WMS_F_DB_PASS="Fst123456"

export WMS_H_DB_SERVER="192.168.60.62"
export WMS_H_DB_PORT="1433"
export WMS_H_DB_NAME="FST_WMS"
export WMS_H_DB_USER="FstWmsReport"
export WMS_H_DB_PASS="Fst123456"

# 方式二：直接修改 config.py 中的默认值（不推荐）
```

### 步骤 4：测试运行

```bash
cd /opt/wms
source venv/bin/activate

# 先测试模拟模式（不需要数据库连接）
WMS_MOCK_MODE=1 python app.py
# 访问 http://服务器IP:5000 应该能看到有模拟数据的看板

# 确认无误后，测试真实数据库模式
python app.py
# 或
python start.py
```

### 步骤 5：使用 Gunicorn 生产运行

```bash
cd /opt/wms
source venv/bin/activate

# 前台运行（测试）
gunicorn app:app --bind 0.0.0.0:5000 --workers 4

# 后台运行
nohup gunicorn app:app --bind 0.0.0.0:5000 --workers 4 --access-logfile /var/log/wms/access.log --error-logfile /var/log/wms/error.log &

# 访问 http://服务器IP:5000
```

### 步骤 6：配置 systemd 服务（推荐，实现开机自启和进程守护）

创建服务文件 `/etc/systemd/system/wms.service`：

```ini
[Unit]
Description=WMS 立体库看板
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/wms
Environment="PATH=/opt/wms/venv/bin"
Environment="WMS_F_DB_SERVER=192.168.11.166"
Environment="WMS_F_DB_PORT=1433"
Environment="WMS_F_DB_NAME=FST_WMS"
Environment="WMS_F_DB_USER=FstWmsReport"
Environment="WMS_F_DB_PASS=Fst123456"
Environment="WMS_H_DB_SERVER=192.168.60.62"
Environment="WMS_H_DB_PORT=1433"
Environment="WMS_H_DB_NAME=FST_WMS"
Environment="WMS_H_DB_USER=FstWmsReport"
Environment="WMS_H_DB_PASS=Fst123456"
ExecStart=/opt/wms/venv/bin/gunicorn app:app --bind 0.0.0.0:5000 --workers 4 --access-logfile /var/log/wms/access.log --error-logfile /var/log/wms/error.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
sudo mkdir -p /var/log/wms
sudo systemctl daemon-reload
sudo systemctl enable wms
sudo systemctl start wms
sudo systemctl status wms    # 查看运行状态
sudo journalctl -u wms -f    # 查看实时日志
```

### 步骤 7：（可选）Nginx 反向代理

如果需要通过域名访问或添加 HTTPS，配置 Nginx：

```nginx
server {
    listen 80;
    server_name wms.your-company.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## 六、环境变量完整参考

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `WMS_MOCK_MODE` | `0` | 设为 `1` 启用模拟模式（脱离数据库运行） |
| `PORT` | `5000` | 应用监听端口 |
| `WMS_F_DB_SERVER` | `192.168.11.166` | F区 SQL Server 地址 |
| `WMS_F_DB_PORT` | `1433` | F区 SQL Server 端口 |
| `WMS_F_DB_NAME` | `FST_WMS` | F区数据库名 |
| `WMS_F_DB_USER` | `FstWmsReport` | F区数据库用户名 |
| `WMS_F_DB_PASS` | `Fst123456` | F区数据库密码 |
| `WMS_H_DB_SERVER` | `192.168.60.62` | H区 SQL Server 地址 |
| `WMS_H_DB_PORT` | `1433` | H区 SQL Server 端口 |
| `WMS_H_DB_NAME` | `FST_WMS` | H区数据库名 |
| `WMS_H_DB_USER` | `FstWmsReport` | H区数据库用户名 |
| `WMS_H_DB_PASS` | `Fst123456` | H区数据库密码 |

---

## 七、常用运维命令

```bash
# 启动服务
sudo systemctl start wms

# 停止服务
sudo systemctl stop wms

# 重启服务
sudo systemctl restart wms

# 查看服务状态
sudo systemctl status wms

# 查看实时日志
sudo journalctl -u wms -f

# 查看最近 100 行日志
sudo journalctl -u wms -n 100

# 重新加载配置（修改 systemd 文件后）
sudo systemctl daemon-reload
sudo systemctl restart wms

# 检查端口是否在监听
ss -tlnp | grep 5000
```

---

## 八、API 路由参考

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 返回总览仪表盘页面 (index.html) |
| `/f` | GET | 返回 F区网格详情页 (f.html) |
| `/h` | GET | 返回 H区网格详情页 (h.html) |
| `/api/overview` | GET | 获取双区总览数据（JSON） |
| `/api/overview?zone=f` | GET | 获取 F区总览数据（JSON） |
| `/api/overview?zone=h` | GET | 获取 H区总览数据（JSON） |
| `/api/locations?zone=f&layer=1` | GET | 获取 F区某层库位状态（JSON） |
| `/api/locations?zone=h&layer=1` | GET | 获取 H区某层库位状态（JSON） |
| `/api/location-detail` | GET | 获取单个库位产品详情（JSON） |
| `/api/search` | GET | 搜索物料（按编码/条码/规格/库位号） |
| `/api/age-buckets` | GET | 获取库龄分段配置 |
| `/api/db-test` | GET | 数据库连接测试 |

---

## 九、常见问题排查

### 1. pyodbc 报错 "Can't open lib 'ODBC Driver 18 for SQL Server'"

**原因：** 未安装 Microsoft ODBC Driver for SQL Server。

**解决：** 执行本文档第 3.4 节的系统级依赖安装步骤。

### 2. 数据库连接超时

**原因：** 防火墙阻止或网络不通。

**解决：** 
```bash
# 测试网络连通性
telnet 192.168.11.166 1433
# 如果 telnet 不通，检查防火墙规则
```

### 3. 页面能打开但数据显示不全

**原因：** 数据库账号权限不足，无法查询 `WMS_HJ_KW` 和 `WMS_RKZY` 表。

**解决：** 确认数据库账号 `FstWmsReport` 对以上两表有 SELECT 权限。

### 4. 端口被占用

```bash
# 查看占用 5000 端口的进程
lsof -i :5000
# 或修改环境变量 PORT=5001 使用其他端口
```

### 5. 模拟模式验证

如果想先不连数据库做快速验证，设置环境变量后启动：

```bash
WMS_MOCK_MODE=1 python app.py
```

---

## 十、快速验证清单

部署完成后按以下步骤验证：

- [ ] `curl http://localhost:5000` 返回 HTML 内容
- [ ] `curl http://localhost:5000/api/overview` 返回总览 JSON 数据
- [ ] `curl http://localhost:5000/api/locations?zone=f&layer=1` 返回 F区库位数据
- [ ] `curl http://localhost:5000/api/locations?zone=h&layer=1` 返回 H区库位数据
- [ ] `curl http://localhost:5000/api/db-test` 返回数据库连接状态
- [ ] 浏览器访问首页，ECharts 图表正常渲染
- [ ] 点击 F区/H区详情页，网格可视化正常显示
- [ ] systemd 服务正常运行（`systemctl status wms`）
- [ ] 重启服务器后服务自动启动

---

