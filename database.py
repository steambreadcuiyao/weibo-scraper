"""SQLite 数据库操作层 — 建表 + CRUD"""

import os
import sqlite3
import json
from datetime import datetime, timezone
from config import DB_PATH


def get_conn():
    """获取数据库连接（启用 WAL 模式 + 外键）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE NOT NULL,
            screen_name TEXT,
            avatar TEXT,
            group_id INTEGER,
            remark TEXT DEFAULT '',
            status INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS account_tags (
            account_uid TEXT NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (account_uid, tag_id),
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS crawl_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            interval_hours INTEGER DEFAULT 6,
            enabled INTEGER DEFAULT 1,
            is_one_time INTEGER DEFAULT 0,
            post_types TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS crawl_task_targets (
            task_id INTEGER NOT NULL,
            uid TEXT NOT NULL,
            PRIMARY KEY (task_id, uid),
            FOREIGN KEY (task_id) REFERENCES crawl_tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mid TEXT NOT NULL,
            uid TEXT NOT NULL,
            screen_name TEXT,
            text TEXT,
            text_html TEXT,
            post_type TEXT DEFAULT 'original',
            created_at TEXT,
            source TEXT,
            reposts_count INTEGER DEFAULT 0,
            comments_count INTEGER DEFAULT 0,
            attitudes_count INTEGER DEFAULT 0,
            is_long_text INTEGER DEFAULT 0,
            pics TEXT,
            video_urls TEXT DEFAULT '[]',
            page_url TEXT DEFAULT '',
            url TEXT,
            crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(mid)
        );

        CREATE INDEX IF NOT EXISTS idx_posts_uid ON posts(uid);
        CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at);
        CREATE INDEX IF NOT EXISTS idx_posts_text ON posts(text);

        CREATE TABLE IF NOT EXISTS crawl_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            uid TEXT NOT NULL,
            status TEXT,
            posts_count INTEGER DEFAULT 0,
            error_msg TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            password TEXT,
            cookie TEXT,
            xsrf_token TEXT,
            expires_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # ── 迁移：旧表补列 ──
    _migrate_add_column(conn, "posts", "post_type", "TEXT DEFAULT 'original'")
    _migrate_add_column(conn, "crawl_tasks", "post_types", "TEXT DEFAULT '[]'")
    _migrate_add_column(conn, "crawl_tasks", "is_one_time", "INTEGER DEFAULT 0")
    _migrate_add_column(conn, "accounts", "remark", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "posts", "video_urls", "TEXT DEFAULT '[]'")
    _migrate_add_column(conn, "posts", "page_url", "TEXT DEFAULT ''")
    conn.commit()
    conn.close()


def _migrate_add_column(conn, table: str, column: str, col_def: str):
    """安全加列，已有则跳过"""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
    except sqlite3.OperationalError:
        pass  # 列已存在


# ── 账号 CRUD ──────────────────────────────────────

def add_account(uid: str, screen_name: str = "", avatar: str = "",
                group_id: int = None) -> dict:
    """添加或更新微博账号，支持分组"""
    conn = get_conn()
    import time
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO accounts (uid, screen_name, avatar, group_id, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(uid) DO UPDATE SET
            screen_name = COALESCE(NULLIF(excluded.screen_name,''), accounts.screen_name),
            avatar = COALESCE(NULLIF(excluded.avatar,''), accounts.avatar),
            group_id = COALESCE(excluded.group_id, accounts.group_id),
            updated_at = excluded.updated_at
    """, (uid, screen_name, avatar, group_id, now))
    conn.commit()
    row = conn.execute("SELECT * FROM accounts WHERE uid=?", (uid,)).fetchone()
    conn.close()
    return dict(row)


def list_accounts(status: int = None, group_id: int = None) -> list[dict]:
    """列出账号，可按状态和分组筛选"""
    conn = get_conn()
    q = """
        SELECT a.*,
               g.name AS group_name,
               (SELECT COUNT(*) FROM posts p WHERE p.uid=a.uid) AS post_count
        FROM accounts a
        LEFT JOIN groups g ON a.group_id = g.id
        WHERE 1=1
    """
    params = []
    if status is not None:
        q += " AND a.status=?"
        params.append(status)
    if group_id is not None:
        q += " AND a.group_id=?"
        params.append(group_id)
    q += " ORDER BY a.updated_at DESC"
    rows = conn.execute(q, params).fetchall()

    # 批量获取标签
    result = []
    for r in rows:
        d = dict(r)
        tags_rows = conn.execute("""
            SELECT t.id, t.name FROM tags t
            JOIN account_tags at ON t.id = at.tag_id
            WHERE at.account_uid = ?
        """, (d["uid"],)).fetchall()
        d["tags"] = [dict(tr) for tr in tags_rows]
        result.append(d)
    conn.close()
    return result


def update_account_status(uid: str, status: int) -> bool:
    """启用/停用账号"""
    conn = get_conn()
    conn.execute("UPDATE accounts SET status=?, updated_at=CURRENT_TIMESTAMP WHERE uid=?",
                 (status, uid))
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


def update_account_group(uid: str, group_id: int) -> bool:
    """设置账号分组（group_id 为 0 或 None 则取消分组）"""
    conn = get_conn()
    if group_id:
        conn.execute("UPDATE accounts SET group_id=?, updated_at=CURRENT_TIMESTAMP WHERE uid=?",
                     (group_id, uid))
    else:
        conn.execute("UPDATE accounts SET group_id=NULL, updated_at=CURRENT_TIMESTAMP WHERE uid=?",
                     (uid,))
    conn.commit()
    conn.close()
    return True


def delete_account(uid: str) -> bool:
    """删除账号及其相关数据"""
    conn = get_conn()
    conn.execute("DELETE FROM account_tags WHERE account_uid=?", (uid,))
    conn.execute("DELETE FROM crawl_task_targets WHERE uid=?", (uid,))
    conn.execute("DELETE FROM posts WHERE uid=?", (uid,))
    conn.execute("DELETE FROM crawl_logs WHERE uid=?", (uid,))
    conn.execute("DELETE FROM accounts WHERE uid=?", (uid,))
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


def get_account(uid: str) -> dict | None:
    """获取单个账号信息"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM accounts WHERE uid=?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── 微博内容 CRUD ──────────────────────────────────

def save_post(post: dict) -> bool:
    """保存单条微博，mid 去重"""
    conn = get_conn()
    pics_json = json.dumps(post.get("pics", []), ensure_ascii=False)
    try:
        conn.execute("""
            INSERT OR IGNORE INTO posts
                (mid, uid, screen_name, text, text_html, post_type, created_at, source,
                 reposts_count, comments_count, attitudes_count,
                 is_long_text, pics, video_urls, page_url, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post["mid"], post["uid"], post.get("screen_name", ""),
            post.get("text", ""), post.get("text_html", ""),
            post.get("post_type", "original"),
            post.get("created_at", ""), post.get("source", ""),
            post.get("reposts_count", 0), post.get("comments_count", 0),
            post.get("attitudes_count", 0), post.get("is_long_text", 0),
            pics_json, json.dumps(post.get("video_urls", []), ensure_ascii=False),
            post.get("page_url", ""), post.get("url", "")
        ))
        conn.commit()
        inserted = conn.total_changes > 0
    except Exception:
        conn.rollback()
        inserted = False
    finally:
        conn.close()
    return inserted


def save_posts(posts: list[dict]) -> int:
    """批量保存微博，返回新增条数"""
    count = 0
    for post in posts:
        if save_post(post):
            count += 1
    return count


def get_max_mid(uid: str) -> int:
    """获取指定账号已存的最大 mid（越大约新），用于爬虫断点"""
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(CAST(mid AS INTEGER)) FROM posts WHERE uid=?", (uid,)
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else 0


def search_posts(uid: str = None, keyword: str = None,
                 start_time: str = None, end_time: str = None,
                 group_id: int = None, tag_id: int = None,
                 post_type: str = None,
                 page: int = 1, page_size: int = 20) -> dict:
    """搜索微博，支持按账号/分组/标签/类型/关键词/时间范围"""
    conn = get_conn()
    conditions = []
    params = []
    joins = []

    if uid:
        conditions.append("p.uid = ?")
        params.append(uid)
    if group_id:
        joins.append("JOIN accounts a ON a.uid = p.uid AND a.group_id = ?")
        params.append(group_id)
    if tag_id:
        joins.append("JOIN account_tags at ON at.account_uid = p.uid AND at.tag_id = ?")
        params.append(tag_id)
    if post_type:
        conditions.append("p.post_type = ?")
        params.append(post_type)
    if keyword:
        conditions.append("p.text LIKE ?")
        params.append(f"%{keyword}%")
    if start_time:
        conditions.append("p.created_at >= ?")
        params.append(start_time)
    if end_time:
        conditions.append("p.created_at <= ?")
        params.append(end_time)

    join_clause = " ".join(joins)
    where = " AND ".join(conditions) if conditions else "1=1"

    count_row = conn.execute(
        f"SELECT COUNT(*) FROM posts p {join_clause} WHERE {where}", params
    ).fetchone()
    total = count_row[0]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT p.* FROM posts p {join_clause} WHERE {where} ORDER BY p.created_at DESC LIMIT ? OFFSET ?",
        params + [page_size, offset]
    ).fetchall()

    conn.close()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
        "data": [dict(r) for r in rows]
    }


def get_posts_by_uid(uid: str, limit: int = 50) -> list[dict]:
    """获取指定账号的最新微博"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM posts WHERE uid=? ORDER BY created_at DESC LIMIT ?",
        (uid, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 爬取日志 ───────────────────────────────────────

def add_crawl_log(uid: str, status: str, posts_count: int = 0,
                  error_msg: str = "", started_at: str = None,
                  task_id: int = None) -> int:
    """添加爬取日志"""
    import time
    conn = get_conn()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute("""
        INSERT INTO crawl_logs (task_id, uid, status, posts_count, error_msg, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (task_id, uid, status, posts_count, error_msg, started_at or now, now))
    conn.commit()
    log_id = cursor.lastrowid
    conn.close()
    return log_id


def list_crawl_logs(limit: int = 20, task_id: int = None, status: str = None,
                    start_time: str = None, end_time: str = None,
                    keyword: str = None) -> list[dict]:
    """获取爬取日志，支持筛选"""
    conn = get_conn()
    q = ("SELECT cl.*, ct.name AS task_name "
         "FROM crawl_logs cl "
         "LEFT JOIN crawl_tasks ct ON ct.id = cl.task_id "
         "WHERE 1=1")
    params = []
    if task_id is not None:
        q += " AND cl.task_id = ?"
        params.append(task_id)
    if status:
        q += " AND cl.status = ?"
        params.append(status)
    if start_time:
        q += " AND cl.finished_at >= ?"
        params.append(start_time)
    if end_time:
        q += " AND cl.finished_at <= ?"
        params.append(end_time)
    if keyword:
        q += " AND (cl.uid LIKE ? OR cl.error_msg LIKE ? OR ct.name LIKE ?)"
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])
    q += " ORDER BY cl.finished_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 凭证管理 ───────────────────────────────────────

def save_credentials(username: str, password: str) -> bool:
    """保存微博登录凭证（明文存储，后续可加加密）"""
    conn = get_conn()
    conn.execute("DELETE FROM credentials")
    conn.execute("""
        INSERT INTO credentials (username, password, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    """, (username, password))
    conn.commit()
    conn.close()
    return True


def save_cookie(cookie: str, xsrf_token: str = "") -> bool:
    """保存登录后的 Cookie（无行则插入，有行则更新）"""
    conn = get_conn()
    # 查是否有行
    row = conn.execute("SELECT id FROM credentials ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        conn.execute(
            "UPDATE credentials SET cookie=?, xsrf_token=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (cookie, xsrf_token, row[0])
        )
    else:
        conn.execute(
            "INSERT INTO credentials (cookie, xsrf_token) VALUES (?, ?)",
            (cookie, xsrf_token)
        )
    conn.commit()
    conn.close()
    return True


def get_credentials() -> dict | None:
    """获取当前凭证信息（密码脱敏）"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM credentials ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    if d.get("password"):
        d["password"] = d["password"][:2] + "***" + d["password"][-1:] if len(d["password"]) > 3 else "***"
    if d.get("cookie"):
        d["cookie"] = d["cookie"][:20] + "..." if len(d["cookie"]) > 23 else d["cookie"]
    return d


def get_raw_credentials() -> dict | None:
    """获取完整凭证（内部使用，不脱敏）"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM credentials ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── 分组管理 ───────────────────────────────────────

def list_groups() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT g.*, COUNT(a.uid) AS account_count FROM groups g "
        "LEFT JOIN accounts a ON a.group_id = g.id GROUP BY g.id ORDER BY g.name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_group(name: str) -> dict:
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO groups (name) VALUES (?)", (name.strip(),))
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        return dict(row)
    except sqlite3.IntegrityError:
        conn.close()
        return {"error": "分组名已存在"}


def delete_group(group_id: int) -> bool:
    conn = get_conn()
    conn.execute("UPDATE accounts SET group_id=NULL WHERE group_id=?", (group_id,))
    conn.execute("DELETE FROM groups WHERE id=?", (group_id,))
    conn.commit()
    conn.close()
    return True


# ── 标签管理 ───────────────────────────────────────

def list_tags() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT t.*, COUNT(at.account_uid) AS account_count FROM tags t "
        "LEFT JOIN account_tags at ON at.tag_id = t.id GROUP BY t.id ORDER BY t.name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_tag(name: str) -> dict:
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (name.strip(),))
        conn.commit()
        row = conn.execute("SELECT * FROM tags WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        return dict(row)
    except sqlite3.IntegrityError:
        conn.close()
        return {"error": "标签名已存在"}


def delete_tag(tag_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM account_tags WHERE tag_id=?", (tag_id,))
    conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    conn.commit()
    conn.close()
    return True


# ── 账号标签关联 ────────────────────────────────────

def set_account_tags(uid: str, tag_ids: list[int]) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM account_tags WHERE account_uid=?", (uid,))
    for tid in tag_ids:
        conn.execute("INSERT OR IGNORE INTO account_tags (account_uid, tag_id) VALUES (?,?)",
                     (uid, tid))
    conn.commit()
    conn.close()
    return True


def get_account_tags(uid: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT t.id, t.name FROM tags t "
        "JOIN account_tags at ON t.id = at.tag_id WHERE at.account_uid=?",
        (uid,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 爬取任务持久化 ──────────────────────────────────

def save_crawl_task(name: str, uids: list[str], interval_hours: int = 6,
                    post_types: list[str] = None, is_one_time: bool = False) -> dict:
    """创建爬取任务，返回任务对象"""
    import json
    pt_json = json.dumps(post_types or [], ensure_ascii=False)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO crawl_tasks (name, interval_hours, post_types, is_one_time) VALUES (?, ?, ?, ?)",
        (name, interval_hours, pt_json, 1 if is_one_time else 0)
    )
    task_id = cur.lastrowid
    for uid in uids:
        conn.execute("INSERT OR IGNORE INTO crawl_task_targets (task_id, uid) VALUES (?,?)",
                     (task_id, uid))
    conn.commit()
    row = conn.execute("SELECT * FROM crawl_tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    result = dict(row)
    result["uids"] = uids
    return result


def update_crawl_task(task_id: int, interval_hours: int = None, enabled: int = None,
                      name: str = None, is_one_time: bool = None,
                      uids: list[str] = None, post_types: list[str] = None) -> bool:
    """更新任务，支持全字段"""
    import json
    conn = get_conn()
    sets = []
    params = []
    if interval_hours is not None:
        sets.append("interval_hours=?"); params.append(interval_hours)
    if enabled is not None:
        sets.append("enabled=?"); params.append(enabled)
    if name is not None:
        sets.append("name=?"); params.append(name)
    if is_one_time is not None:
        sets.append("is_one_time=?"); params.append(1 if is_one_time else 0)
    if post_types is not None:
        sets.append("post_types=?"); params.append(json.dumps(post_types, ensure_ascii=False))
    if sets:
        params.append(task_id)
        conn.execute(f"UPDATE crawl_tasks SET {','.join(sets)} WHERE id=?", params)
        conn.commit()
    if uids is not None:
        conn.execute("DELETE FROM crawl_task_targets WHERE task_id=?", (task_id,))
        for uid in uids:
            conn.execute("INSERT OR IGNORE INTO crawl_task_targets (task_id, uid) VALUES (?,?)",
                         (task_id, uid))
        conn.commit()
    conn.close()
    return True


def delete_crawl_task(task_id: int) -> bool:
    """删除任务及其关联"""
    conn = get_conn()
    conn.execute("DELETE FROM crawl_task_targets WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM crawl_tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    return True


def list_crawl_tasks() -> list[dict]:
    """列出所有爬取任务（含目标账号列表）"""
    conn = get_conn()
    tasks = conn.execute("SELECT * FROM crawl_tasks ORDER BY id").fetchall()
    result = []
    for t in tasks:
        d = dict(t)
        targets = conn.execute(
            "SELECT a.uid, a.screen_name FROM crawl_task_targets ct "
            "JOIN accounts a ON a.uid = ct.uid WHERE ct.task_id=?",
            (d["id"],)
        ).fetchall()
        d["targets"] = [dict(r) for r in targets]
        d["uids"] = [r["uid"] for r in targets]
        try: d["post_types"] = json.loads(d.get("post_types") or "[]")
        except: d["post_types"] = []
        result.append(d)
    conn.close()
    return result


def get_crawl_task(task_id: int) -> dict | None:
    """获取单个任务"""
    conn = get_conn()
    t = conn.execute("SELECT * FROM crawl_tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        conn.close()
        return None
    d = dict(t)
    targets = conn.execute(
        "SELECT a.uid, a.screen_name FROM crawl_task_targets ct "
        "JOIN accounts a ON a.uid = ct.uid WHERE ct.task_id=?",
        (d["id"],)
    ).fetchall()
    d["targets"] = [dict(r) for r in targets]
    d["uids"] = [r["uid"] for r in targets]
    try: d["post_types"] = json.loads(d.get("post_types") or "[]")
    except: d["post_types"] = []
    conn.close()
    return d


# ── 统计 ───────────────────────────────────────────

def get_stats() -> dict:
    """获取整体统计数据"""
    conn = get_conn()
    total_accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    active_accounts = conn.execute("SELECT COUNT(*) FROM accounts WHERE status=1").fetchone()[0]
    total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    latest_crawl = conn.execute(
        "SELECT MAX(finished_at) FROM crawl_logs WHERE status='success'"
    ).fetchone()[0]

    # 按账号统计
    account_stats = conn.execute("""
        SELECT a.uid, a.screen_name, COUNT(p.id) as cnt
        FROM accounts a LEFT JOIN posts p ON a.uid = p.uid
        GROUP BY a.uid ORDER BY cnt DESC
    """).fetchall()

    conn.close()
    return {
        "total_accounts": total_accounts,
        "active_accounts": active_accounts,
        "total_posts": total_posts,
        "latest_crawl": latest_crawl,
        "account_stats": [dict(r) for r in account_stats]
    }
