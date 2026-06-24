"""Playwright 二维码登录微博

流程:
1. 前端调用 start_qrcode_login() → 返回二维码 base64
2. 用户扫码
3. 前端轮询 check_qrcode_status() → 检测登录完成
4. 登录成功自动保存 Cookie 和 storage state
"""

import re
import os
import time
import json
import base64
import logging
import threading

os.environ.pop("NODE_OPTIONS", None)

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

import database as db
from config import LOGIN_TIMEOUT, PLAYWRIGHT_HEADLESS

logger = logging.getLogger("weibo.login")

AUTH_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "auth_state.json")

# ── 全局状态：二维码登录 ──────────────────────────
_qr_lock = threading.Lock()
_qr_state = {
    "status": "idle",       # idle | waiting | success | failed
    "playwright": None,
    "browser": None,
    "context": None,
    "page": None,
    "result": None,
    "message": "",
    "thread": None,
}


def _extract_cookie(context) -> str:
    """从 Playwright context 提取 Cookie 字符串"""
    cookies = context.cookies()
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _extract_xsrf(cookie_str: str) -> str:
    m = re.search(r"XSRF-TOKEN=([^;]+)", cookie_str)
    return m.group(1) if m else ""


def _save_storage_state(context) -> None:
    os.makedirs(os.path.dirname(AUTH_STATE_FILE), exist_ok=True)
    with open(AUTH_STATE_FILE, "w") as f:
        json.dump(context.storage_state(), f)
    logger.info(f"storage state 已保存: {AUTH_STATE_FILE}")


# ── 1. 自动恢复已保存状态 ──────────────────────────

def try_auto_login_with_saved_state() -> dict | None:
    """用 storage_state 文件无需扫码自动恢复登录"""
    if not os.path.exists(AUTH_STATE_FILE):
        return None

    logger.info("尝试用已保存的登录状态恢复...")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=PLAYWRIGHT_HEADLESS,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = browser.new_context(storage_state=AUTH_STATE_FILE)
        page = context.new_page()
        page.goto("https://weibo.com", wait_until="domcontentloaded", timeout=15000)

        # 检测登录态
        try:
            page.wait_for_selector("text=首页", timeout=8000)
        except PwTimeout:
            page.wait_for_selector('[class*="search"] input, input[class*="search"]', timeout=5000)

        cookie_str = _extract_cookie(context)
        xsrf_token = _extract_xsrf(cookie_str)
        sub = (re.search(r"SUB=([^;]+)", cookie_str) or [None, ""])[1]

        db.save_cookie(cookie_str, xsrf_token)
        _save_storage_state(context)

        logger.info(f"恢复登录成功 (SUB={sub[:12]}...)")
        return {"cookie": cookie_str, "xsrf_token": xsrf_token, "sub": sub}

    except Exception as e:
        logger.warning(f"恢复登录失败: {e}")
        return None
    finally:
        try: browser.close()
        except: pass
        try: pw.stop()
        except: pass


# ── 2. 二维码登录 ─────────────────────────────────

def _browser_thread_worker():
    """在后台线程中: 打开页面 → 截图二维码 → 轮询登录 → 完成"""
    global _qr_state

    try:
        pw = sync_playwright().start()
        _qr_state["playwright"] = pw

        browser = pw.chromium.launch(
            headless=PLAYWRIGHT_HEADLESS,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        _qr_state["browser"] = browser

        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        _qr_state["context"] = context
        page = context.new_page()
        _qr_state["page"] = page

        # 打开 SSO 登录页
        login_url = (
            "https://passport.weibo.com/sso/signin"
            "?entry=miniblog&source=miniblog"
            "&url=https%3A%2F%2Fweibo.com%2F"
        )
        logger.info("二维码登录: 打开 SSO 页面...")
        page.goto(login_url, wait_until="domcontentloaded", timeout=LOGIN_TIMEOUT * 1000)
        time.sleep(3)

        # 截取二维码区域
        qrcode_img = None
        for sel in [
            'img[class*="qrcode"]',
            'img[class*="qr"]',
            'img[src*="qrcode"]',
            'img[src*="qr"]',
            '.qrcode img',
            '[class*="code"] img',
            'img[width="160"]',
            'img[width="180"]',
            'img[width="200"]',
            'img[width*="15"]',
            'img[width="200"]',
            'canvas',
        ]:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible(timeout=2000):
                    qrcode_img = el.screenshot()
                    logger.info(f"二维码截图成功: {sel} ({len(qrcode_img)} bytes)")
                    break
            except Exception:
                continue

        if not qrcode_img:
            # 降级: 截整个页面
            logger.warning("未找到二维码元素，截取整页")
            qrcode_img = page.screenshot()

        _qr_state["qrcode"] = base64.b64encode(qrcode_img).decode()
        _qr_state["status"] = "waiting"
        _qr_state["message"] = "请使用微博 App 扫描二维码"
        logger.info("二维码就绪，等待扫码...")

        # 轮询等待扫码完成 (最长 3 分钟)
        for i in range(90):
            time.sleep(2)
            current_url = page.url

            if "weibo.com" in current_url and "passport" not in current_url:
                # 登录成功
                cookie_str = _extract_cookie(context)
                xsrf_token = _extract_xsrf(cookie_str)
                sub = (re.search(r"SUB=([^;]+)", cookie_str) or [None, ""])[1]

                if not sub:
                    logger.warning("已跳转但未找到 SUB cookie，再等一下...")
                    continue

                result = {"cookie": cookie_str, "xsrf_token": xsrf_token, "sub": sub}
                db.save_cookie(cookie_str, xsrf_token)
                _save_storage_state(context)

                _qr_state["status"] = "success"
                _qr_state["result"] = result
                _qr_state["message"] = "登录成功"
                logger.info(f"扫码登录成功 (SUB={sub[:12]}...)")
                return

            # 检查二维码是否过期
            try:
                expired = page.locator('text=二维码已过期, text=重新获取, text=已失效')
                if expired.first.is_visible(timeout=500):
                    _qr_state["status"] = "failed"
                    _qr_state["message"] = "二维码已过期，请重新获取"
                    logger.error("二维码已过期")
                    return
            except Exception:
                pass

        # 超时
        _qr_state["status"] = "failed"
        _qr_state["message"] = "扫码超时（3分钟），请重试"
        logger.error("二维码登录超时")

    except Exception as e:
        logger.error(f"二维码登录异常: {e}")
        _qr_state["status"] = "failed"
        _qr_state["message"] = str(e)

    finally:
        _cleanup_browser()


def _cleanup_browser():
    """清理 Playwright 资源"""
    global _qr_state
    try:
        if _qr_state.get("browser"):
            _qr_state["browser"].close()
    except Exception:
        pass
    try:
        if _qr_state.get("playwright"):
            _qr_state["playwright"].stop()
    except Exception:
        pass
    _qr_state["browser"] = None
    _qr_state["context"] = None
    _qr_state["page"] = None
    _qr_state["playwright"] = None


def start_qrcode_login() -> dict:
    """
    启动二维码登录流程，返回二维码 base64 图片数据

    返回: {"status": "ok", "qrcode": "<base64>", "message": "..."}
           {"status": "error", "message": "..."}
    """
    global _qr_state

    if not _qr_lock.acquire(blocking=False):
        return {"status": "error", "message": "登录流程已在运行中"}

    try:
        # 重置状态
        _cleanup_browser()
        _qr_state = {
            "status": "starting",
            "playwright": None,
            "browser": None,
            "context": None,
            "page": None,
            "result": None,
            "message": "正在生成二维码...",
            "thread": None,
            "qrcode": None,
        }

        # 后台线程执行
        t = threading.Thread(target=_browser_thread_worker, daemon=True)
        _qr_state["thread"] = t
        t.start()

        # 等待二维码截图完成（最多 15 秒）
        for _ in range(15):
            time.sleep(1)
            if _qr_state.get("qrcode"):
                return {
                    "status": "ok",
                    "qrcode": _qr_state["qrcode"],
                    "message": _qr_state.get("message", ""),
                }
            if _qr_state.get("status") == "failed":
                return {
                    "status": "error",
                    "message": _qr_state.get("message", "无法生成二维码"),
                }

        return {"status": "error", "message": "生成二维码超时"}

    except Exception as e:
        _qr_state["status"] = "failed"
        _qr_state["message"] = str(e)
        return {"status": "error", "message": str(e)}
    finally:
        _qr_lock.release()


def check_qrcode_status() -> dict:
    """
    检查二维码登录状态

    返回:
      {"status": "waiting", "message": "..."}   — 等待扫码
      {"status": "success", "cookie": "...", "sub": "..."} — 登录成功
      {"status": "expired", "message": "..."}   — 二维码过期
      {"status": "failed", "message": "..."}    — 失败
      {"status": "idle", "message": "..."}      — 未启动
    """
    global _qr_state
    s = _qr_state["status"]

    if s == "success" and _qr_state.get("result"):
        r = _qr_state["result"]
        return {
            "status": "success",
            "message": "登录成功",
            "sub": r["sub"][:12] + "...",
        }
    elif s == "failed":
        return {"status": "expired", "message": _qr_state.get("message", "登录失败")}
    elif s == "waiting":
        return {"status": "waiting", "message": _qr_state.get("message", "等待扫码...")}
    elif s == "starting":
        return {"status": "waiting", "message": "正在加载二维码..."}
    else:
        return {"status": "idle", "message": "未启动登录"}


def reset_qrcode_login() -> dict:
    """取消/重置二维码登录"""
    global _qr_state
    if _qr_lock.locked():
        _qr_lock.release()
    _cleanup_browser()
    _qr_state["status"] = "idle"
    _qr_state["result"] = None
    _qr_state["qrcode"] = None
    logger.info("二维码登录已重置")
    return {"status": "ok", "message": "已重置"}


# ── 3. Cookie 校验 ────────────────────────────────

def check_cookie_valid(cookie_str: str = None) -> bool:
    import requests
    if not cookie_str:
        creds = db.get_raw_credentials()
        if not creds or not creds.get("cookie"):
            return False
        cookie_str = creds["cookie"]

    try:
        # 请求微博首页，检查是否包含登录态标识
        r = requests.get(
            "https://weibo.com/",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Cookie": cookie_str,
            },
            timeout=10,
            allow_redirects=True,
        )
        if r.status_code != 200:
            return False
        # 已登录的首页不会包含 passport 重定向，且包含用户配置数据
        return "passport" not in r.url and ('$CONFIG' in r.text or 'uid' in r.text)
    except Exception as e:
        logger.warning(f"Cookie 校验异常: {e}")
        return False


# ── 4. 获取有效 Cookie ────────────────────────────

def get_valid_cookie() -> dict | None:
    """
    获取有效 Cookie，优先级: 保存的 storage → 数据库
    不再自动触发密码登录，改为返回 None 让调用方提示用户扫码
    """
    # 1) storage state
    saved = try_auto_login_with_saved_state()
    if saved and check_cookie_valid(saved["cookie"]):
        return {"cookie": saved["cookie"], "xsrf_token": saved["xsrf_token"]}

    # 2) 数据库
    creds = db.get_raw_credentials()
    if creds and creds.get("cookie"):
        if check_cookie_valid(creds["cookie"]):
            return {"cookie": creds["cookie"], "xsrf_token": creds.get("xsrf_token", "")}

    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    db.init_db()
    # 先试恢复
    result = get_valid_cookie()
    if result:
        print("恢复登录成功!")
    else:
        print("需要扫码登录，准备生成二维码...")
        qr = start_qrcode_login()
        if qr["status"] == "ok":
            # 保存 base64 到文件用于测试
            with open("/tmp/qrcode_base64.txt", "w") as f:
                f.write(qr["qrcode"])
            print("二维码已生成，等待扫码...")
            import sys; sys.stdout.flush()
            for _ in range(90):
                time.sleep(2)
                s = check_qrcode_status()
                if s["status"] == "success":
                    print("登录成功!")
                    break
                elif s["status"] in ("expired", "failed"):
                    print(f"失败: {s['message']}")
                    break
