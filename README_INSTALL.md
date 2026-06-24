# 微博爬虫监控系统 — 安装说明

## 环境要求

- Windows 10 / 11 或 Windows Server
- Python 3.11 及以上
- 能访问 weibo.com 的网络

## 安装步骤

### 1. 安装 Python

下载 [python.org](https://www.python.org/downloads/)，安装时 **必须勾选「Add Python to PATH」**。

验证是否安装成功：

```powershell
python --version
```

### 2. 解压项目

把 `weibo-monitor.zip` 解压到任意目录，例如 `D:\weibo-monitor`。

### 3. 安装依赖

```powershell
cd D:\weibo-monitor
pip install flask requests apscheduler playwright
playwright install chromium
```

### 4. 启动服务

```powershell
python app.py
```

看到以下输出说明启动成功：

```
 * Running on http://0.0.0.0:5000
```

浏览器打开 **http://localhost:5000** 即可访问管理后台。

---

## 首次使用

1. **扫码登录** → 点击「获取二维码」，用微博 App 扫码
2. **添加账号** → 输入微博 UID 或主页链接，如 `https://weibo.com/u/2803301701`
3. **创建爬取任务** → 点击「添加任务」，选择账号、设置间隔
4. **执行任务** → 点击 ▶ 开始爬取
5. **搜索数据** → 在「数据搜索」页按条件检索

---

## 常见问题

### 浏览器打不开 localhost:5000？

检查防火墙是否拦截了 5000 端口，或改用 `http://127.0.0.1:5000`。

### 扫码登录失败？

确保网络能访问 weibo.com，Chrome 浏览器已通过 playwright 安装。

### 数据存在哪？

SQLite 单文件，路径为 `data/weibo.db`，备份直接拷贝这个文件。

### 怎么后台运行？

关闭 CMD 窗口服务就停了。如需常驻，可以把启动命令写入 .bat 文件放入 Windows 启动文件夹。

---

## 端口修改

默认端口 5000，如需修改：

```powershell
# 临时
set FLASK_PORT=8080
python app.py

# 或直接改 config.py 中的 FLASK_PORT
```

---

## 项目结构

```
weibo-monitor/
├── app.py              # Flask 主入口
├── config.py           # 配置常量
├── database.py         # SQLite 数据库操作
├── crawler.py          # 微博爬虫核心
├── scheduler.py        # APScheduler 定时调度
├── login.py            # 扫码登录（Playwright）
├── logger_setup.py     # 日志系统
├── static/
│   └── index.html      # 管理后台前端（单文件）
├── data/
│   ├── weibo.db         # SQLite 数据库
│   ├── auth_state.json  # 登录态
│   └── weibo.log        # 运行日志
└── README_INSTALL.md
```
