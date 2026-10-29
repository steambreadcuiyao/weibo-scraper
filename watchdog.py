"""微博爬虫服务看门狗

由 Windows 计划任务定时调用（建议每 1 分钟）。
职责：探测服务是否健康，不健康则拉起。

健康判定（双重）：
  1. HTTP 探针：请求 /api/stats 返回 200 视为进程可用
  2. 端口兜底：HTTP 失败但端口在监听 → 判定为「假死」，杀掉重建

设计要点：
  - 单实例锁：避免多个看门狗并发拉起导致重复启动
  - 冷却期：拉起后 N 秒内不重复拉起，防止崩溃循环疯狂重启
  - 日志：写入 data/watchdog.log，带轮转
"""

import os
import sys
import json
import time
import socket
import logging
import logging.handlers
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_FILE = os.path.join(DATA_DIR, "watchdog.log")
LOCK_FILE = os.path.join(DATA_DIR, "watchdog.lock")
STAMP_FILE = os.path.join(DATA_DIR, "watchdog.laststart")
STREAK_FILE = os.path.join(DATA_DIR, "watchdog.streak")

# 服务自身标准输出的重定向文件（werkzeug / traceback 走这里，不走 logging）
STDOUT_LOG = os.path.join(DATA_DIR, "service-stdout.log")
STDOUT_MAX_BYTES = 5 * 1024 * 1024   # 5MB 轮转
STDOUT_BACKUPS = 2                   # 保留 2 个历史文件（≈15MB 上限）

MAX_FAIL_STREAK = 3         # 连续拉起失败几次后进入冷却

PYTHON = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\weibo\Scripts\pythonw.exe"
APP_PY = os.path.join(BASE_DIR, "app.py")
HOST = "127.0.0.1"
PORT = 5000

COOLDOWN_SEC = 120          # 拉起后冷却时间，防止崩溃循环
HTTP_TIMEOUT = 8            # HTTP 探针超时
LOCK_STALE_SEC = 300        # 锁文件视为过期的时长


def setup_logger() -> logging.Logger:
    os.makedirs(DATA_DIR, exist_ok=True)
    logger = logging.getLogger("watchdog")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        h = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=512 * 1024, backupCount=3, encoding="utf-8"
        )
        h.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(h)
    return logger


def port_listening() -> bool:
    """5000 端口是否有进程在监听"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        return s.connect_ex((HOST, PORT)) == 0


def http_healthy() -> bool:
    """HTTP 探针：/api/stats 返回 200"""
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/stats",
                                    timeout=HTTP_TIMEOUT) as r:
            if r.status != 200:
                return False
            body = r.read(4096).decode("utf-8", errors="replace")
            return '"ok"' in body or '"data"' in body
    except Exception:
        return False


def acquire_lock(logger) -> bool:
    """单实例锁。返回 True 表示拿到锁，可以继续"""
    if os.path.exists(LOCK_FILE):
        try:
            age = time.time() - os.path.getmtime(LOCK_FILE)
            if age < LOCK_STALE_SEC:
                logger.info(f"另一看门狗正在运行（锁 {age:.0f}s），本次跳过")
                return False
            logger.warning(f"发现过期锁（{age:.0f}s），强制接管")
        except OSError:
            pass
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except OSError as e:
        logger.error(f"创建锁失败: {e}")
        return False


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def read_fail_streak() -> int:
    """读取「拉起后仍不健康」的连续失败次数"""
    if not os.path.exists(STREAK_FILE):
        return 0
    try:
        with open(STREAK_FILE) as f:
            return int(f.read().strip() or "0")
    except (OSError, ValueError):
        return 0


def write_fail_streak(n: int):
    try:
        with open(STREAK_FILE, "w") as f:
            f.write(str(n))
    except OSError:
        pass


def in_cooldown(logger) -> bool:
    """是否应跳过本次拉起。

    冷却只针对「崩溃循环」：即看门狗已连续多次拉起、但服务每次都撑不到下一轮。
    正常的外部单次崩溃（服务健康运行了很久后被杀）不受冷却限制，立即恢复。

    判据：连续失败次数 >= MAX_FAIL_STREAK，且距上次尝试未超过 COOLDOWN_SEC。
    """
    streak = read_fail_streak()
    if streak < MAX_FAIL_STREAK:
        return False

    if not os.path.exists(STAMP_FILE):
        return False
    try:
        elapsed = time.time() - os.path.getmtime(STAMP_FILE)
        if elapsed < COOLDOWN_SEC:
            logger.warning(
                f"检测到崩溃循环（连续 {streak} 次拉起失败），"
                f"冷却 {elapsed:.0f}s / {COOLDOWN_SEC}s 后重试"
            )
            return True
    except OSError:
        pass
    return False


def kill_app_processes(logger):
    """结束所有 weibo app.py 进程"""
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\""
            " | Where-Object { $_.CommandLine -like '*weibo*app.py*' }"
            " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue;"
            " Write-Output $_.ProcessId }"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=60)
        killed = [x for x in r.stdout.split() if x.strip().isdigit()]
        if killed:
            logger.info(f"已结束残留进程: {', '.join(killed)}")
    except Exception as e:
        logger.warning(f"结束进程时出错: {e}")


def rotate_stdout_log(logger):
    """service-stdout.log 超过上限则轮转。

    这个文件接收服务的 stdout/stderr（werkzeug 访问日志、未捕获 traceback），
    不走 logging，因此 RotatingFileHandler 管不到它，必须自己处理，否则会无限膨胀。
    轮转时机选在「拉起新进程之前」：此刻旧进程已退出（或刚被杀掉），
    文件句柄不冲突，rename 能稳定成功。
    """
    try:
        if not os.path.exists(STDOUT_LOG):
            return
        size = os.path.getsize(STDOUT_LOG)
        if size < STDOUT_MAX_BYTES:
            return

        oldest = f"{STDOUT_LOG}.{STDOUT_BACKUPS}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(STDOUT_BACKUPS - 1, 0, -1):
            src = f"{STDOUT_LOG}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{STDOUT_LOG}.{i + 1}")
        os.replace(STDOUT_LOG, f"{STDOUT_LOG}.1")
        logger.info(f"service-stdout.log 已轮转（{size / 1024 / 1024:.1f}MB）")
    except OSError as e:
        logger.warning(f"service-stdout.log 轮转失败（继续追加）: {e}")


def start_app(logger) -> bool:
    """以独立进程方式启动服务"""
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008
    flags = DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

    rotate_stdout_log(logger)

    out = open(STDOUT_LOG, "a", encoding="utf-8", errors="replace")
    try:
        p = subprocess.Popen(
            [PYTHON, APP_PY],
            cwd=BASE_DIR,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        # 记录拉起时间戳，用于冷却判定
        with open(STAMP_FILE, "w") as f:
            f.write(str(int(time.time())))
        logger.info(f"已拉起服务 PID={p.pid}")
        return True
    except Exception as e:
        logger.error(f"启动服务失败: {e}")
        return False
    finally:
        # 子进程已继承句柄，父进程这份要关掉，否则每次拉起泄漏一个句柄
        try:
            out.close()
        except OSError:
            pass


def wait_healthy(logger, timeout=45) -> bool:
    """等待服务变健康"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if http_healthy():
            logger.info(f"服务健康确认（耗时 {timeout - int(deadline - time.time())}s）")
            return True
        time.sleep(3)
    logger.error(f"拉起后 {timeout}s 内未通过健康检查")
    return False


def main():
    logger = setup_logger()

    # 健康：清零失败计数，什么都不做
    if http_healthy():
        if read_fail_streak() > 0:
            logger.info("服务已恢复健康，失败计数清零")
        write_fail_streak(0)
        logger.info("服务健康，无需处理")
        return 0

    # 不健康：先看端口
    zombie = port_listening()
    if zombie:
        logger.warning("HTTP 探针失败但端口在监听 → 判定为假死，将重建")

    if not acquire_lock(logger):
        return 0

    try:
        if in_cooldown(logger):
            return 0

        if zombie:
            kill_app_processes(logger)
            time.sleep(3)

        start_app(logger)

        if wait_healthy(logger):
            write_fail_streak(0)
        else:
            streak = read_fail_streak() + 1
            write_fail_streak(streak)
            logger.error(f"拉起失败，连续失败次数 = {streak}")
    finally:
        release_lock()

    return 0


if __name__ == "__main__":
    sys.exit(main())
