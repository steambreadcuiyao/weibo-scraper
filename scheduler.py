"""APScheduler 多任务调度器 — 任务维度（一个任务可爬多个账号）"""

import logging
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import SCHEDULER_DEFAULT_INTERVAL

logger = logging.getLogger("weibo.scheduler")

scheduler = BackgroundScheduler()

# ── 运行状态追踪 ──────────────────────────────────
_running_tasks: dict = {}  # task_id → {status, name, done, total, current_uid, total_posts, started_at}


def _crawl_job(task_id: int):
    """爬取一个任务的所有目标账号"""
    import database as db
    import crawler
    import time as _time

    task = db.get_crawl_task(task_id)
    if not task or not task.get("uids"):
        logger.warning(f"任务 {task_id} 无目标账号，跳过")
        return

    name = task.get("name", f"任务#{task_id}")
    post_types = task.get("post_types") or []
    uids = task["uids"]
    logger.info(f"爬取 [{name}] 开始 ({len(uids)} 个账号)")

    _running_tasks[task_id] = {"status": "running", "name": name, "done": 0, "total": len(uids),
                                "current_uid": "", "total_posts": 0,
                                "started_at": _time.strftime("%Y-%m-%d %H:%M:%S")}

    total = 0
    for i, uid in enumerate(uids):
        _running_tasks[task_id]["current_uid"] = uid
        _running_tasks[task_id]["done"] = i
        result = crawler.crawl_user(uid, task_id=task_id, post_types=post_types or None)
        total += result.get("posts_count", 0)
        _running_tasks[task_id]["total_posts"] = total
        _running_tasks[task_id]["done"] = i + 1

    _running_tasks[task_id]["status"] = "done"
    _running_tasks[task_id]["current_uid"] = ""
    _running_tasks[task_id]["total_posts"] = total
    logger.info(f"爬取 [{name}] 完成: 新增 {total} 条")

    # 写入汇总记录
    import time as _time2
    db.add_crawl_log("", "success", total, started_at=_running_tasks[task_id]["started_at"],
                     task_id=task_id)

    # 延迟清理
    t_clean = threading.Timer(60, lambda: _running_tasks.pop(task_id, None))
    t_clean.daemon = True
    t_clean.start()


# ── 内部 ──────────────────────────────────────────

def _make_job_id(task_id: int) -> str:
    return f"crawl_task_{task_id}"


def get_running_tasks() -> list[dict]:
    """获取正在执行的任务状态"""
    return [{"task_id": tid, **info} for tid, info in _running_tasks.items()]


# ── 初始化 ────────────────────────────────────────

def init_scheduler():
    """初始化调度器，恢复数据库中的启用任务"""
    import database as db

    if not scheduler.running:
        scheduler.start()

    tasks = db.list_crawl_tasks()
    for t in tasks:
        if t["enabled"] and not t.get("is_one_time", 0):
            _schedule_job(t["id"], t["interval_hours"])

    logger.info(f"调度器已启动，恢复 {len(tasks)} 个任务 ({sum(1 for t in tasks if t['enabled'])} 个运行中)")


# ── 内部 ──────────────────────────────────────────

def _schedule_job(task_id: int, interval_minutes: int):
    scheduler.add_job(
        _crawl_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id=_make_job_id(task_id),
        args=[task_id],
        replace_existing=True,
    )


def _remove_job(task_id: int):
    try:
        scheduler.remove_job(_make_job_id(task_id))
    except Exception:
        pass


# ── 任务 CRUD ─────────────────────────────────────

def add_task(name: str, uids: list[str], interval_minutes: int = None,
             post_types: list[str] = None, is_one_time: bool = False) -> dict:
    """创建爬取任务"""
    import database as db
    if interval_minutes is None:
        interval_minutes = SCHEDULER_DEFAULT_INTERVAL
    interval_minutes = max(1, min(interval_minutes, 4320))  # 1分钟 ~ 72小时

    task = db.save_crawl_task(name, uids, interval_minutes, post_types, is_one_time)
    tid = task["id"]

    if is_one_time:
        logger.info(f"创建单次爬取任务 [{name}] id={tid} uids={len(uids)}，等待手动执行")
        return {"ok": True, "message": f"单次任务 [{name}] 已创建，点击 ▶ 执行 ({len(uids)}个账号)"}
    else:
        _schedule_job(tid, interval_minutes)
        logger.info(f"创建爬取任务 [{name}] id={tid} uids={len(uids)} 间隔={interval_minutes}min")
        return {"ok": True, "message": f"已创建任务 [{name}] ({len(uids)}个账号)"}


def remove_task(task_id: int) -> dict:
    import database as db
    task = db.get_crawl_task(task_id)
    name = task["name"] if task else f"#{task_id}"
    _remove_job(task_id)
    db.delete_crawl_task(task_id)
    logger.info(f"已删除爬取任务: [{name}] id={task_id}")
    return {"ok": True, "message": f"已删除任务 [{name}]"}


def update_task(task_id: int, interval_minutes: int = None, enabled: int = None,
                name: str = None, is_one_time: bool = None, uids: list[str] = None,
                post_types: list[str] = None) -> dict:
    import database as db
    db.update_crawl_task(task_id, interval_hours=interval_minutes, enabled=enabled,
                         name=name, is_one_time=is_one_time, uids=uids, post_types=post_types)

    if enabled is not None:
        if enabled:
            task = db.get_crawl_task(task_id)
            _schedule_job(task_id, task["interval_hours"] if task else interval_minutes or 360)
        else:
            _remove_job(task_id)

    if is_one_time is True:
        _remove_job(task_id)

    if interval_minutes is not None:
        task = db.get_crawl_task(task_id)
        if task and task["enabled"] and not task.get("is_one_time", 0):
            _schedule_job(task_id, interval_minutes)

    return {"ok": True, "message": "任务已更新"}


def pause_task(task_id: int) -> dict:
    import database as db
    db.update_crawl_task(task_id, enabled=0)
    _remove_job(task_id)
    return {"ok": True, "message": "任务已暂停"}


def resume_task(task_id: int) -> dict:
    import database as db
    task = db.get_crawl_task(task_id)
    if not task:
        return {"ok": False, "message": "任务不存在"}
    db.update_crawl_task(task_id, enabled=1)
    _schedule_job(task_id, task["interval_hours"])
    return {"ok": True, "message": "任务已恢复"}


def run_task_now(task_id: int) -> dict:
    import database as db
    if task_id in _running_tasks and _running_tasks[task_id].get("status") == "running":
        return {"ok": False, "message": "该任务正在执行中，请等待完成"}
    task = db.get_crawl_task(task_id)
    name = task["name"] if task else f"#{task_id}"
    t = threading.Thread(target=_crawl_job, args=[task_id], daemon=True)
    t.start()
    logger.info(f"手动触发: [{name}] id={task_id}")
    return {"ok": True, "message": f"已触发 [{name}] 立即执行"}


# ── 查询 ──────────────────────────────────────────

def list_tasks() -> list[dict]:
    import database as db
    tasks = db.list_crawl_tasks()
    result = []
    for t in tasks:
        job = scheduler.get_job(_make_job_id(t["id"]))
        result.append({
            "id": t["id"],
            "name": t["name"],
            "interval_hours": t["interval_hours"],
            "enabled": bool(t["enabled"]),
            "is_one_time": bool(t.get("is_one_time", 0)),
            "targets": t.get("targets", []),
            "target_count": len(t.get("uids", [])),
            "next_run": str(job.next_run_time) if job and job.next_run_time else None,
        })
    return result


def get_scheduler_status() -> dict:
    tasks = list_tasks()
    return {"running": scheduler.running, "task_count": len(tasks), "tasks": tasks}


# ── 维度解析（从 app.py 重用）──────────────────────

def resolve_uids_by_dimension(data: dict) -> list[str]:
    import database as db
    if data.get("uid"):
        return [data["uid"].strip()]
    if data.get("uids"):
        return [u.strip() for u in data["uids"] if u.strip()]
    if data.get("group_id"):
        accounts = db.list_accounts(status=1, group_id=int(data["group_id"]))
        return [a["uid"] for a in accounts]
    if data.get("tag_id"):
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT a.uid FROM accounts a "
            "JOIN account_tags at ON at.account_uid = a.uid "
            "WHERE at.tag_id = ? AND a.status = 1",
            (int(data["tag_id"]),)
        ).fetchall()
        conn.close()
        return [r["uid"] for r in rows]
    return []
