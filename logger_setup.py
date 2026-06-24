"""统一日志系统 — 文件轮转 + 多级别 + 结构化格式"""

import os
import logging
from logging.handlers import RotatingFileHandler

# 日志文件
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG_FILE = os.path.join(LOG_DIR, "system.log")
LOG_MAX_BYTES = 5 * 1024 * 1024   # 5MB 轮转
LOG_BACKUP_COUNT = 10              # 保留最近 10 个

# 统一格式
LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)-14s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def setup_logging(log_level: int = logging.INFO):
    """初始化全局日志系统（只执行一次）"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    os.makedirs(LOG_DIR, exist_ok=True)

    # 根 logger
    root = logging.getLogger()
    root.setLevel(log_level)

    # 清除已有 handler
    root.handlers.clear()

    # ── 文件 handler（轮转）──
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(file_handler)

    # ── 控制台 handler ──
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)  # 控制台只显示 WARNING+
    console.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(console)

    # 校准第三方库日志级别
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.getLogger("weibo").info("日志系统初始化完成 | 文件: %s", LOG_FILE)


def get_log_file_path() -> str:
    return LOG_FILE


def read_logs(lines: int = 200, level: str = None, keyword: str = None,
              start_time: str = None, end_time: str = None,
              module: str = None) -> dict:
    """
    从日志文件读取最近的日志行，支持过滤

    参数:
        lines:   读取最近 N 行
        level:   过滤级别 (DEBUG/INFO/WARNING/ERROR)
        keyword: 关键词搜索
        start_time / end_time: 时间范围 (格式: YYYY-MM-DD HH:MM)
        module:  模块名过滤 (如 "weibo.login")

    返回:
        {"total": N, "lines": [...]}
    """
    if not os.path.exists(LOG_FILE):
        return {"total": 0, "lines": []}

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    # 倒序取最近 N 行
    recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
    recent.reverse()

    result = []
    for line in recent:
        line = line.rstrip("\n\r")
        if not line:
            continue

        # 过滤级别
        if level:
            if f"| {level:5s} " not in line:
                continue

        # 过滤模块
        if module:
            # 日志格式: 时间 | 级别 | 模块名 | 消息
            if f"| {module} " not in line:
                continue

        # 过滤关键词
        if keyword:
            if keyword.lower() not in line.lower():
                continue

        # 过滤时间范围
        if start_time or end_time:
            date_part = line[:19]  # YYYY-MM-DD HH:MM:SS
            if start_time and date_part < start_time:
                continue
            if end_time and date_part > end_time:
                continue

        result.append(line)

    return {"total": len(result), "lines": result}
