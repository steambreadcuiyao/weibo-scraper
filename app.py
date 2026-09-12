"""Flask 主应用 — API 路由 + 静态页面服务"""

import os
import logging
import threading
from flask import Flask, request, jsonify, send_from_directory

# ⚠️ 日志系统必须在其他模块导入前初始化
import logger_setup
logger_setup.setup_logging()

import database as db
import crawler
import login as login_mod
from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG

# 初始化数据库
db.init_db()

app = Flask(__name__, static_folder="static", static_url_path="")

logger = logging.getLogger("weibo.app")

# 爬取锁，防止并发爬取
crawl_lock = threading.Lock()
crawl_running = False


# ── 页面 ──────────────────────────────────────────

@app.route("/")
def index():
    """主页面"""
    return send_from_directory("static", "index.html")


# ── 账号管理 API ──────────────────────────────────

@app.route("/api/accounts", methods=["GET"])
def api_list_accounts():
    """列出所有账号"""
    try:
        accounts = db.list_accounts()
        return jsonify({"ok": True, "data": accounts})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/accounts", methods=["POST"])
def api_add_account():
    """添加账号"""
    data = request.json or {}
    raw = data.get("uid", "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "请提供微博 UID 或主页链接"}), 400

    uid = crawler.extract_uid_from_input(raw)
    if not uid:
        return jsonify({"ok": False, "error": "无法解析 UID，请输入数字 UID 或 https://weibo.com/u/xxx 格式的链接"}), 400

    group_id = data.get("group_id")
    try:
        info = crawler.fetch_user_info(uid)
        screen_name = info.get("screen_name", "") if info else ""
        avatar = info.get("avatar", "") if info else ""

        account = db.add_account(uid, screen_name, avatar, group_id)
        return jsonify({"ok": True, "data": account})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/accounts/<uid>", methods=["GET"])
def api_get_account(uid):
    """获取单个账号详情"""
    try:
        account = db.get_account(uid)
        if not account:
            return jsonify({"ok": False, "error": "账号不存在"}), 404
        tags = db.get_account_tags(uid)
        account["tags"] = tags
        return jsonify({"ok": True, "data": account})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/accounts/<uid>", methods=["PUT"])
def api_update_account(uid):
    """更新账号信息 {group_id, remark, screen_name}"""
    data = request.json or {}
    try:
        # 分组
        if "group_id" in data:
            db.update_account_group(uid, data["group_id"])
        # 标签
        if "tag_ids" in data:
            db.set_account_tags(uid, data["tag_ids"])
        # 备注（直接 SQL）
        if "remark" in data:
            conn = db.get_conn()
            conn.execute("UPDATE accounts SET remark=?, updated_at=CURRENT_TIMESTAMP WHERE uid=?",
                         (data["remark"], uid))
            conn.commit()
            conn.close()
        if "screen_name" in data:
            conn = db.get_conn()
            conn.execute("UPDATE accounts SET screen_name=?, updated_at=CURRENT_TIMESTAMP WHERE uid=?",
                         (data["screen_name"], uid))
            conn.commit()
            conn.close()
        return jsonify({"ok": True, "message": "已更新"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/accounts/<uid>", methods=["DELETE"])
def api_delete_account(uid):
    """删除账号"""
    try:
        ok = db.delete_account(uid)
        return jsonify({"ok": ok, "message": "删除成功" if ok else "账号不存在"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/accounts/<uid>/status", methods=["PUT"])
def api_update_account_status(uid):
    """更新账号状态（启用/停用）"""
    data = request.json or {}
    status = data.get("status", 1)
    try:
        ok = db.update_account_status(uid, status)
        return jsonify({"ok": ok, "message": "状态更新成功" if ok else "账号不存在"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/accounts/<uid>/group", methods=["PUT"])
def api_update_account_group(uid):
    """设置账号分组"""
    data = request.json or {}
    group_id = data.get("group_id")
    try:
        db.update_account_group(uid, group_id)
        return jsonify({"ok": True, "message": "分组已更新"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/accounts/<uid>/tags", methods=["GET"])
def api_get_account_tags(uid):
    """获取账号标签"""
    try:
        tags = db.get_account_tags(uid)
        return jsonify({"ok": True, "data": tags})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/accounts/<uid>/tags", methods=["PUT"])
def api_set_account_tags(uid):
    """设置账号标签 {tag_ids: [1,2,3]}"""
    data = request.json or {}
    tag_ids = data.get("tag_ids", [])
    try:
        db.set_account_tags(uid, tag_ids)
        return jsonify({"ok": True, "message": "标签已更新"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 分组管理 API ──────────────────────────────────

@app.route("/api/groups", methods=["GET"])
def api_list_groups():
    try:
        groups = db.list_groups()
        return jsonify({"ok": True, "data": groups})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/groups", methods=["POST"])
def api_add_group():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "请输入分组名"}), 400
    try:
        result = db.add_group(name)
        if "error" in result:
            return jsonify({"ok": False, "error": result["error"]}), 409
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/groups/<int:group_id>", methods=["DELETE"])
def api_delete_group(group_id):
    try:
        db.delete_group(group_id)
        return jsonify({"ok": True, "message": "分组已删除"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 标签管理 API ──────────────────────────────────

@app.route("/api/tags", methods=["GET"])
def api_list_tags():
    try:
        tags = db.list_tags()
        return jsonify({"ok": True, "data": tags})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tags", methods=["POST"])
def api_add_tag():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "请输入标签名"}), 400
    try:
        result = db.add_tag(name)
        if "error" in result:
            return jsonify({"ok": False, "error": result["error"]}), 409
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def api_delete_tag(tag_id):
    try:
        db.delete_tag(tag_id)
        return jsonify({"ok": True, "message": "标签已删除"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 凭证管理 API ──────────────────────────────────

@app.route("/api/credentials", methods=["GET"])
def api_get_credentials():
    """查看凭证状态（脱敏）"""
    creds = db.get_credentials()
    has_cookie = bool(creds and creds.get("cookie"))
    return jsonify({
        "ok": True,
        "data": creds,
        "has_cookie": has_cookie,
        "cookie_valid": login_mod.check_cookie_valid() if has_cookie else False
    })


@app.route("/api/credentials", methods=["POST"])
def api_save_credentials():
    """已废弃：二维码登录无需密码。该接口保留兼容但返回提示。"""
    return jsonify({"ok": False, "error": "请使用二维码扫码登录，无需配置密码"}), 400


# ── 二维码登录 API ──────────────────────────────────

@app.route("/api/login/qrcode", methods=["POST"])
def api_login_qrcode():
    """获取二维码图片（base64），前端展示给用户扫码"""
    try:
        result = login_mod.start_qrcode_login()
        if result["status"] == "ok":
            return jsonify({"ok": True, "qrcode": result["qrcode"], "message": result["message"]})
        else:
            return jsonify({"ok": False, "error": result["message"]}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/login/qrcode/status", methods=["GET"])
def api_login_qrcode_status():
    """轮询扫码状态"""
    try:
        status = login_mod.check_qrcode_status()
        return jsonify({"ok": True, "data": status})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/login/qrcode", methods=["DELETE"])
def api_login_qrcode_reset():
    """重置/取消二维码登录"""
    try:
        result = login_mod.reset_qrcode_login()
        return jsonify({"ok": True, "message": result["message"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 爬取控制 API ──────────────────────────────────

@app.route("/api/crawl", methods=["POST"])
def api_crawl():
    """手动触发爬取"""
    global crawl_running

    if crawl_running:
        return jsonify({"ok": False, "error": "爬取正在进行中，请稍后再试"}), 409

    data = request.json or {}
    uid = data.get("uid", "").strip()

    if not crawl_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "爬取正在进行中"}), 409

    crawl_running = True

    try:
        if uid:
            result = crawler.crawl_user(uid)
            results = [result]
        else:
            results = crawler.crawl_all()

        total = sum(r["posts_count"] for r in results)
        return jsonify({
            "ok": True,
            "message": f"爬取完成，共新增 {total} 条微博",
            "data": {"results": results, "total_new": total}
        })
    except Exception as e:
        logger.error(f"爬取出错: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        crawl_running = False
        crawl_lock.release()


@app.route("/api/crawl/status", methods=["GET"])
def api_crawl_status():
    """查看爬取状态，支持日志筛选"""
    task_id = request.args.get("task_id", type=int)
    log_status = request.args.get("status") or None
    start_time = request.args.get("start_time") or None
    end_time = request.args.get("end_time") or None
    keyword = request.args.get("keyword") or None
    limit = request.args.get("limit", 50, type=int)

    return jsonify({
        "ok": True,
        "data": {
            "running": crawl_running,
            "last_logs": db.list_crawl_logs(
                limit=limit, task_id=task_id, status=log_status,
                start_time=start_time, end_time=end_time, keyword=keyword
            )
        }
    })


# ── 搜索 API ──────────────────────────────────────

@app.route("/api/posts", methods=["GET"])
def api_search_posts():
    """搜索微博"""
    uid = request.args.get("uid", "").strip() or None
    keyword = request.args.get("keyword", "").strip() or None
    start_time = request.args.get("start_time", "").strip() or None
    end_time = request.args.get("end_time", "").strip() or None
    group_id = request.args.get("group_id", type=int)
    tag_id = request.args.get("tag_id", type=int)
    post_type = request.args.get("post_type", "").strip() or None
    page = max(1, int(request.args.get("page", 1)))
    page_size = min(100, max(1, int(request.args.get("page_size", 20))))

    try:
        result = db.search_posts(
            uid=uid, keyword=keyword,
            start_time=start_time, end_time=end_time,
            group_id=group_id, tag_id=tag_id, post_type=post_type,
            page=page, page_size=page_size
        )
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 统计 API ──────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
def api_stats():
    """获取整体统计"""
    try:
        stats = db.get_stats()
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 定时任务 API ──────────────────────────────────

@app.route("/api/scheduler/tasks", methods=["GET"])
def api_scheduler_list():
    try:
        from scheduler import list_tasks
        return jsonify({"ok": True, "data": list_tasks()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduler/tasks", methods=["POST"])
def api_scheduler_add():
    """添加爬取任务，支持按账号/分组/标签维度批量
    {name, uid/uids/group_id/tag_id, interval}"""
    data = request.json or {}
    interval = data.get("interval")
    try:
        from scheduler import resolve_uids_by_dimension, add_task
        uids = resolve_uids_by_dimension(data)
        if not uids:
            return jsonify({"ok": False, "error": "请提供 uid / uids / group_id / tag_id"}), 400
        name = data.get("name", "").strip()
        if not name:
            # 自动生成名称
            group_id = data.get("group_id")
            tag_id = data.get("tag_id")
            if group_id:
                grp = db.list_groups()
                gn = next((g["name"] for g in grp if g["id"] == int(group_id)), f"分组{group_id}")
                name = f"分组: {gn}"
            elif tag_id:
                tags = db.list_tags()
                tn = next((t["name"] for t in tags if t["id"] == int(tag_id)), f"标签{tag_id}")
                name = f"标签: {tn}"
            elif len(uids) == 1:
                acc = db.get_account(uids[0])
                name = acc["screen_name"] or uids[0] if acc else uids[0]
            else:
                name = f"批量任务 ({len(uids)}个)"
        post_types = data.get("post_types") or []
        is_one_time = data.get("is_one_time", False)
        result = add_task(name, uids, interval, post_types, is_one_time)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduler/tasks/<int:task_id>", methods=["DELETE"])
def api_scheduler_remove(task_id):
    try:
        from scheduler import remove_task
        return jsonify(remove_task(task_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduler/tasks/<int:task_id>", methods=["PUT"])
def api_scheduler_update(task_id):
    data = request.json or {}
    try:
        from scheduler import update_task
        result = update_task(
            task_id,
            interval_minutes=data.get("interval"),
            name=data.get("name"),
            is_one_time=data.get("is_one_time"),
            uids=data.get("uids"),
            post_types=data.get("post_types")
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"更新任务 #{task_id} 失败: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduler/tasks/<int:task_id>/pause", methods=["POST"])
def api_scheduler_pause(task_id):
    try:
        from scheduler import pause_task
        return jsonify(pause_task(task_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduler/tasks/<int:task_id>/resume", methods=["POST"])
def api_scheduler_resume(task_id):
    try:
        from scheduler import resume_task
        return jsonify(resume_task(task_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduler/tasks/<int:task_id>/run", methods=["POST"])
def api_scheduler_run(task_id):
    try:
        from scheduler import run_task_now
        return jsonify(run_task_now(task_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduler/running", methods=["GET"])
def api_scheduler_running():
    try:
        from scheduler import get_running_tasks
        return jsonify({"ok": True, "data": get_running_tasks()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 导出 API ──────────────────────────────────────

@app.route("/api/export", methods=["GET"])
def api_export():
    """导出微博为 CSV"""
    import io
    import csv

    uid = request.args.get("uid", "").strip() or None
    keyword = request.args.get("keyword", "").strip() or None
    start_time = request.args.get("start_time", "").strip() or None
    end_time = request.args.get("end_time", "").strip() or None
    group_id = request.args.get("group_id", type=int)
    tag_id = request.args.get("tag_id", type=int)
    post_type = request.args.get("post_type", "").strip() or None

    try:
        result = db.search_posts(
            uid=uid, keyword=keyword,
            start_time=start_time, end_time=end_time,
            group_id=group_id, tag_id=tag_id, post_type=post_type,
            page=1, page_size=100000
        )
        posts = result["data"]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["mid", "uid", "screen_name", "text", "created_at", "source",
                          "reposts_count", "comments_count", "attitudes_count", "url"])
        for p in posts:
            writer.writerow([
                p.get("mid", ""), p.get("uid", ""), p.get("screen_name", ""),
                p.get("text", ""), p.get("created_at", ""), p.get("source", ""),
                p.get("reposts_count", 0), p.get("comments_count", 0),
                p.get("attitudes_count", 0), p.get("url", "")
            ])

        output.seek(0)
        from flask import Response
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=weibo_export.csv"}
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 系统日志 API ──────────────────────────────────

@app.route("/api/logs", methods=["GET"])
def api_logs():
    """查询系统日志，支持多条件过滤"""
    lines = request.args.get("lines", 200, type=int)
    level = request.args.get("level") or None
    keyword = request.args.get("keyword") or None
    start_time = request.args.get("start_time") or None
    end_time = request.args.get("end_time") or None
    module = request.args.get("module") or None

    lines = min(1000, max(10, lines))

    try:
        result = logger_setup.read_logs(
            lines=lines,
            level=level,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
            module=module,
        )
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/logs/modules", methods=["GET"])
def api_log_modules():
    """获取所有日志模块名列表"""
    return jsonify({
        "ok": True,
        "data": ["weibo.app", "weibo.login", "weibo.crawler", "weibo.scheduler"]
    })


# ── 启动入口 ──────────────────────────────────────

def _detach_console():
    """把本进程从 Windows 控制台解绑，避免关闭启动它的 CMD/终端窗口时服务被连带杀掉。

    Windows 上进程默认绑定到启动它的控制台，窗口关闭时系统会向控制台内所有
    进程发送 CTRL_CLOSE_EVENT，Python 收到后直接退出。调用 FreeConsole() 后
    进程不再属于任何控制台，关窗口不再影响服务。
    日志仍正常写入 data/system.log。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
        logger.info("已脱离控制台（关闭启动窗口不会再终止服务）")
    except Exception as e:
        logger.warning(f"脱离控制台失败（不影响运行）: {e}")


def start_server():
    """启动 Flask 服务"""
    _detach_console()

    # 初始化调度器
    try:
        from scheduler import init_scheduler
        init_scheduler()
        logger.info("定时任务调度器已初始化")
    except Exception as e:
        logger.warning(f"调度器初始化失败: {e}")

    logger.info(f"微博爬虫监控系统启动: http://{FLASK_HOST}:{FLASK_PORT}")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, use_reloader=False)


if __name__ == "__main__":
    start_server()
