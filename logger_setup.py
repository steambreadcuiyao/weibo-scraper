"""统一日志系统 — 文件轮转 + 多级别 + 结构化格式"""

import os
import logging
from logging.handlers import RotatingFileHandler

# 日志文件
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG_FILE = os.path.join(LOG_DIR, "system.log")
LOG_MAX_BYTES = 5 * 1024 * 1024   # 5MB 轮转
LOG_BACKUP_COUNT = 5               # 保留最近 5 个（5×5MB ≈ 25MB 上限）

# 统一格式
LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)-14s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


class SafeRotatingFileHandler(RotatingFileHandler):
    """Windows 下轮转失败不致命。

    RotatingFileHandler 在多进程同时写同一文件时不安全：一个进程 rename 时另一个
    进程还持有该文件的句柄，Windows 会抛 PermissionError。原生的做法会让这次
    轮转彻底失败（日志继续往老文件里追加，直到重启），虽然不至于崩服务，但会让
    文件无限变大。这里吞掉 OSError，下一次写日志时会再次尝试轮转。
    """

    def doRollover(self):
        try:
            super().doRollover()
        except OSError as e:
            # 另一个进程还占着文件，跳过本次，下次再试
            logging.getLogger("weibo").debug("日志轮转跳过: %s", e)


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
    file_handler = SafeRotatingFileHandler(
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
    console.setLevel(log_level)  # 控制台与文件同级别，否则 werkzeug 无法启动
    console.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(console)

    # 校准第三方库日志级别
    # 注意：werkzeug 日志级别必须在 Flask 启动后才可修改，否则会导致服务器静默崩溃
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.getLogger("weibo").info("日志系统初始化完成 | 文件: %s", LOG_FILE)


def get_log_file_path() -> str:
    return LOG_FILE


def _log_files() -> list:
    """返回存在的日志文件，按「从旧到新」排序。

    RotatingFileHandler 的命名：system.log（当前）→ system.log.1（次新）
    → system.log.2 …… 序号越大越旧。
    """
    files = []
    for i in range(LOG_BACKUP_COUNT, 0, -1):
        p = f"{LOG_FILE}.{i}"
        if os.path.exists(p):
            files.append(p)
    if os.path.exists(LOG_FILE):
        files.append(LOG_FILE)
    return files


def _tail_lines(path: str, n: int) -> list:
    """从文件尾部读最后 n 行。

    按字节块从后往前读，避免把 5MB 文件整体载入内存；多字节字符可能被块边界
    截断，但先拼字节再统一 decode，所以不会乱码。
    """
    if n <= 0:
        return []
    block = 64 * 1024
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            data = b""
            # 多读一行，防止末行被块边界切开后误判
            while pos > 0 and data.count(b"\n") <= n:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
    except OSError:
        return []

    lines = [ln.decode("utf-8", errors="replace") for ln in data.split(b"\n")]
    lines = [ln for ln in lines if ln.strip()]
    return lines[-n:]


def read_logs(lines: int = 200, level: str = None, keyword: str = None,
              start_time: str = None, end_time: str = None,
              module: str = None) -> dict:
    """
    从日志文件读取最近的日志行，支持过滤

    读取范围覆盖轮转后的历史文件（system.log.1 ~ system.log.N），
    当前文件不足 N 行时自动向更早的备份文件续读。

    参数:
        lines:   读取最近 N 行
        level:   过滤级别 (DEBUG/INFO/WARNING/ERROR)
        keyword: 关键词搜索
        start_time / end_time: 时间范围 (格式: YYYY-MM-DD HH:MM)
        module:  模块名过滤 (如 "weibo.login")

    返回:
        {"total": N, "lines": [...]}
    """
    files = _log_files()
    if not files:
        return {"total": 0, "lines": []}

    # 从最新文件往回取，凑够 lines 行即止
    collected = []
    for path in reversed(files):
        need = lines - len(collected)
        if need <= 0:
            break
        part = _tail_lines(path, need)
        if part:
            collected = part + collected

    # 倒序取最近 N 行
    recent = collected[-lines:] if len(collected) > lines else collected
    recent = list(recent)
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
