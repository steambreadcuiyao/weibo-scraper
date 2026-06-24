"""微博爬虫核心 — API 请求 + 数据解析 + 去重存储"""

import re
import time
import random
import logging
import requests

import database as db
import login as login_mod
from config import CRAWL_MAX_PAGES, CRAWL_PAGE_SIZE, CRAWL_MIN_DELAY, CRAWL_MAX_DELAY

logger = logging.getLogger("weibo.crawler")


def _clean_html(text_html: str) -> str:
    """去除 HTML 标签，提取纯文本"""
    if not text_html:
        return ""
    # 去除 <br /> <br/> 替换为换行
    text = re.sub(r"<br\s*/?>", "\n", text_html)
    # 去除其他 HTML 标签
    text = re.sub(r"<[^>]+>", "", text)
    # 去除微博话题标记（保留文字）
    # 清理多余空白
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text


def _get_post_type(mblog: dict) -> str:
    """识别帖子类型"""
    if mblog.get("retweeted_status"):
        return "repost"

    page_info = mblog.get("page_info") or {}
    if page_info:
        obj_type = str(page_info.get("object_type", page_info.get("type", "")))
        if obj_type in ("article", "3"):
            return "article"
        elif obj_type in ("video", "11"):
            return "video"

    if mblog.get("pics"):
        return "image"

    return "original"


def _normalize_time(weibo_time: str) -> str:
    """将微博时间格式转为 YYYY-MM-DD HH:MM:SS
    例: 'Wed May 20 17:43:22 +0800 2026' → '2026-05-20 17:43:22'
    """
    import email.utils
    from datetime import datetime, timezone, timedelta
    if not weibo_time:
        return ""
    # 尝试标准解析
    try:
        # 格式: Wed May 20 17:43:22 +0800 2026
        t = email.utils.parsedate_to_datetime(weibo_time)
        # 转为本地时间 (UTC+8)
        tz = timezone(timedelta(hours=8))
        t = t.astimezone(tz)
        return t.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return weibo_time  # 无法解析则原样返回


def _parse_card(card: dict, uid: str, screen_name: str) -> dict | None:
    """解析单条微博数据，兼容 card 嵌套和直接平铺两种结构"""
    # 兼容 card 嵌套 (旧版) 和直接平铺 (新版)
    mblog = card.get("mblog")
    if mblog is None:
        mblog = card  # 新版 API 直接平铺，item 本身就是 mblog

    mid = mblog.get("mid") or mblog.get("id")
    if not mid:
        return None

    text_html = mblog.get("text", "")
    text = _clean_html(text_html)

    post = {
        "mid": str(mid),
        "uid": uid,
        "screen_name": screen_name or mblog.get("user", {}).get("screen_name", ""),
        "text": text,
        "text_html": text_html,
        "post_type": _get_post_type(mblog),
        "created_at": _normalize_time(mblog.get("created_at", "")),
        "source": mblog.get("source", ""),
        "reposts_count": mblog.get("reposts_count", 0),
        "comments_count": mblog.get("comments_count", 0),
        "attitudes_count": mblog.get("attitudes_count", 0),
        "is_long_text": 1 if mblog.get("isLongText") else 0,
        "pics": [],
        "url": f"https://weibo.com/{uid}/{mid}",
    }

    # 提取图片
    pics = mblog.get("pics") or []
    for pic in pics:
        if isinstance(pic, dict):
            large = pic.get("large", {})
            post["pics"].append(large.get("url") or pic.get("url", ""))

    # 提取视频 / 文章链接
    page_info = mblog.get("page_info") or {}
    video_urls = []
    page_url = ""
    if page_info:
        media = page_info.get("media_info") or {}
        # 主视频
        stream = media.get("stream_url") or media.get("stream_url_hd") or ""
        if stream:
            video_urls.append(stream)
        # 多视频卡片（微博故事/合集等）
        for key in ("stream_url_hd", "mp4_720p", "mp4_hd", "mp4_sd"):
            if media.get(key) and media[key] not in video_urls:
                video_urls.append(media[key])
        page_url = page_info.get("page_url") or ""

    post["video_urls"] = video_urls
    post["page_url"] = page_url

    # 处理转发微博
    retweeted = mblog.get("retweeted_status")
    if retweeted:
        rt_text = _clean_html(retweeted.get("text", ""))
        if rt_text:
            post["text"] = f"//@{retweeted.get('user',{}).get('screen_name','')}: {rt_text}"

    return post


def fetch_long_text(mid: str, cookie: str, xsrf_token: str) -> str:
    """获取长微博全文"""
    headers = _build_headers(cookie, xsrf_token)
    try:
        r = requests.get(
            "https://weibo.com/ajax/statuses/longtext",
            params={"id": mid},
            headers=headers,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("data", {}).get("longTextContent", "")
    except Exception as e:
        logger.warning(f"获取长文失败 (mid={mid}): {e}")
    return ""


def _build_headers(cookie: str, xsrf_token: str, referer: str = "https://weibo.com/") -> dict:
    """构建请求头"""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "X-Xsrf-Token": xsrf_token,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }


def crawl_user(uid: str, max_pages: int = None, task_id: int = None,
               post_types: list[str] = None) -> dict:
    """
    爬取单个用户的微博
    post_types: 可选过滤，只存匹配类型的帖子，None 表示全部
    """
    if max_pages is None:
        max_pages = CRAWL_MAX_PAGES

    # 获取有效 Cookie
    auth = login_mod.get_valid_cookie()
    if not auth:
        return {"status": "error", "uid": uid, "posts_count": 0,
                "error": "无法获取有效 Cookie，请先配置登录凭证"}

    cookie = auth["cookie"]
    xsrf_token = auth.get("xsrf_token", "")

    # 获取账号信息
    account = db.get_account(uid)
    screen_name = account.get("screen_name", "") if account else ""

    total_saved = 0
    page = 1
    since_id = ""

    # ── 断点去重：查已存最大 mid ──
    max_mid = db.get_max_mid(uid)
    duplicate_hit = False

    def _filter_type(post: dict) -> bool:
        """类型过滤，None 或空列表 = 全收"""
        if not post_types:
            return True
        return post.get("post_type", "original") in post_types

    logger.info(f"开始爬取 uid={uid} 最多 {max_pages} 页, 已存 max_mid={max_mid}")

    # 记录开始日志
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    log_id = None

    headers = _build_headers(cookie, xsrf_token, f"https://weibo.com/u/{uid}")

    try:
        while page <= max_pages:
            # 构建请求参数
            params = {"uid": uid, "page": page, "feature": 0, "count": CRAWL_PAGE_SIZE}
            if since_id:
                params["since_id"] = since_id

            # 请求 API
            try:
                r = requests.get(
                    "https://weibo.com/ajax/statuses/mymblog",
                    params=params,
                    headers=headers,
                    timeout=15
                )
            except requests.RequestException as e:
                logger.error(f"请求失败 (uid={uid}, page={page}): {e}")
                break

            # 检查响应
            if r.status_code == 403:
                logger.warning("403 Forbidden — Cookie 可能已失效")
                # 尝试重新登录（二维码方式）
                auth = login_mod.get_valid_cookie()
                if auth:
                    cookie = auth["cookie"]
                    xsrf_token = auth.get("xsrf_token", "")
                    headers = _build_headers(cookie, xsrf_token, f"https://weibo.com/u/{uid}")
                    continue
                else:
                    logger.error("Cookie 失效且无法恢复，请扫码重新登录")
                    break

            if r.status_code != 200:
                logger.warning(f"HTTP {r.status_code} (uid={uid}, page={page})")
                break

            try:
                data = r.json()
            except Exception:
                logger.warning(f"响应解析失败 (uid={uid}, page={page})")
                break

            # 检查数据有效性
            if not data.get("data") or not data["data"].get("list"):
                # 可能没有更多数据了
                logger.info(f"无更多数据 (uid={uid}, page={page})")
                break

            posts_raw = data["data"]["list"]
            logger.info(f"第 {page} 页: 获取 {len(posts_raw)} 条")

            # ── 断点去重：MID 递增，<= max_mid 的都是旧帖 ──
            new_posts_raw = []
            for card in posts_raw:
                mblog = card.get("mblog") or card  # 兼容嵌套和平铺
                mid_str = str(mblog.get("mid") or mblog.get("id", ""))
                try:
                    mid_int = int(mid_str)
                except ValueError:
                    new_posts_raw.append(card)
                    continue

                if max_mid > 0 and mid_int <= max_mid:
                    duplicate_hit = True
                    logger.info(f"  碰到旧帖 mid={mid_int} <= max_mid={max_mid}，停止翻页")
                    break
                new_posts_raw.append(card)

            if duplicate_hit:
                posts_parsed = []
                for card in new_posts_raw:
                    post = _parse_card(card, uid, screen_name)
                    if post:
                        if post["is_long_text"]:
                            time.sleep(random.uniform(0.5, 1.0))
                            long_text = fetch_long_text(post["mid"], cookie, xsrf_token)
                            if long_text:
                                post["text"] = _clean_html(long_text)
                        if _filter_type(post):
                            posts_parsed.append(post)

                saved = db.save_posts(posts_parsed) if posts_parsed else 0
                total_saved += saved
                logger.info(f"断点终止: 新增 {saved} 条")
                break

            # 解析并保存
            posts_parsed = []
            for card in posts_raw:
                post = _parse_card(card, uid, screen_name)
                if post:
                    # 长文处理
                    if post["is_long_text"]:
                        time.sleep(random.uniform(0.5, 1.0))
                        long_text = fetch_long_text(post["mid"], cookie, xsrf_token)
                        if long_text:
                            post["text"] = _clean_html(long_text)

                    if _filter_type(post):
                        posts_parsed.append(post)

            if posts_parsed:
                saved = db.save_posts(posts_parsed)
                total_saved += saved
                logger.info(f"  保存 {saved} 条新微博 (共 {total_saved} 条)")

            # 翻页
            since_id = data["data"].get("since_id", "")

            page += 1

            # 随机延迟
            delay = random.uniform(CRAWL_MIN_DELAY, CRAWL_MAX_DELAY)
            time.sleep(delay)

        # 记录成功日志
        db.add_crawl_log(uid, "success", total_saved, started_at=started_at, task_id=task_id)

        result = {"status": "success", "uid": uid, "posts_count": total_saved}

    except Exception as e:
        logger.error(f"爬取异常 (uid={uid}): {e}")
        db.add_crawl_log(uid, "error", total_saved, str(e), started_at=started_at, task_id=task_id)
        result = {"status": "error", "uid": uid, "posts_count": total_saved, "error": str(e)}

    return result


def crawl_all() -> list[dict]:
    """爬取所有启用的账号"""
    accounts = db.list_accounts(status=1)  # 只爬取启用的
    results = []
    for account in accounts:
        result = crawl_user(account["uid"])
        results.append(result)
    return results


def extract_uid_from_input(raw: str) -> str | None:
    """
    从用户输入中提取微博 UID
    支持:
      - 纯数字 UID: "1234567890"
      - 主页链接: "https://weibo.com/u/1234567890"
      - 域名链接: "https://weibo.com/n/昵称" (需要再查)
    """
    raw = raw.strip()
    # 纯数字
    if raw.isdigit():
        return raw
    # 从 URL 提取
    match = re.search(r"weibo\.com/u/(\d+)", raw)
    if match:
        return match.group(1)
    # /n/ 格式需要解析页面，返回 None 让调用方处理
    match = re.search(r"weibo\.com/n/([^/?]+)", raw)
    if match:
        return None  # 需要进一步解析
    return None


def fetch_user_info(uid: str) -> dict | None:
    """通过 UID 获取用户基本信息"""
    auth = login_mod.get_valid_cookie()
    if not auth:
        return None

    headers = _build_headers(auth["cookie"], auth.get("xsrf_token", ""))
    try:
        r = requests.get(
            "https://weibo.com/ajax/profile/info",
            params={"uid": uid},
            headers=headers,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            user = data.get("user", {})
            return {
                "uid": str(user.get("id", uid)),
                "screen_name": user.get("screen_name", ""),
                "avatar": user.get("avatar_hd", user.get("profile_image_url", "")),
            }
    except Exception as e:
        logger.warning(f"获取用户信息失败 (uid={uid}): {e}")
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    db.init_db()
    print("爬虫模块就绪")
