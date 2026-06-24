"""全局配置常量"""

import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# SQLite 数据库
DB_PATH = os.path.join(DATA_DIR, "weibo.db")

# Flask
FLASK_HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.environ.get("FLASK_PORT", 5000))
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

# 爬虫配置
CRAWL_PAGE_SIZE = 10          # 每页微博数（API 固定）
CRAWL_MAX_PAGES = 10          # 每账号每次最多爬取页数
CRAWL_MIN_DELAY = 1.5         # 请求最小间隔(秒)
CRAWL_MAX_DELAY = 3.0         # 请求最大间隔(秒)

# 定时任务
SCHEDULER_DEFAULT_INTERVAL = 360   # 默认每 360 分钟（6 小时）

# 登录相关
LOGIN_TIMEOUT = 60             # 登录超时(秒)
LOGIN_CHECK_INTERVAL = 30      # Cookie 过期前多少分钟提前刷新(分钟)

# Playwright
PLAYWRIGHT_HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
