import asyncio
import json
import random
import ipaddress
from urllib.parse import urlparse
from typing import Optional, Union, Any, Dict, List, Tuple
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from curl_cffi import AsyncSession
from config import SEARCH_TIMEOUT, RANDOM_DELAY_MIN, RANDOM_DELAY_MAX

DEFAULT_PAGES = 3
MAX_CONCURRENT_REQUESTS = 5

non_artical_website = [
    r"youtube\.com/watch\?",
    r"youtu\.be/",
    r"bilibili\.com/video/",
    r"v\.qq\.com/",
    r"youku\.com/v_show/",
    r"play\.google\.com/store/apps",
    r"apps\.apple\.com/",
    r"steampowered\.com/",
    r"open\.spotify\.com/",
    r"music\.apple\.com/",
    r"discogs\.com/",
    r"amazon\.",
    r"recochoku\.jp/",
    r"hmv\.co\.jp/",
    r"newegg"
]

def is_valid_ip(ip_str: str) -> bool:
    try:
        ipaddress.ip_address(ip_str)
        return True
    except ValueError:
        return False

def is_valid_port(port: Union[int, str]) -> bool:
    try:
        p = int(port)
        return 1 <= p <= 65535
    except (TypeError, ValueError):
        return False

def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return False
    try:
        if parsed.port is not None:
            if not 1 <= parsed.port <= 65535:
                return False
    except ValueError:
        return False
    return True

def validate_params(
    query: str,
    base_ip: Optional[str],
    port: Optional[Union[int, str]],
    base_url: Optional[str],
    pages: int
) -> Optional[str]:
    if not query or not isinstance(query, str):
        return "query 必须是非空字符串"

    if base_ip is not None or port is not None:
        if base_ip is None or port is None:
            return "base_ip 和 port 必须同时提供"
        if not is_valid_ip(base_ip):
            return f"base_ip 格式无效: {base_ip}"
        if not is_valid_port(port):
            return f"port 格式无效（应为1-65535的整数）: {port}"
    elif base_url is not None:
        if not is_valid_url(base_url):
            return f"base_url 格式无效（需为有效的 http(s) URL）: {base_url}"
    else:
        return "必须提供 base_ip+port 或 base_url 之一"

    if not isinstance(pages, int) or pages <= 0:
        return "pages 必须是正整数"

    return None

def build_search_url(
    base_ip: Optional[str],
    port: Optional[Union[int, str]],
    base_url: Optional[str]
) -> str:
    if base_ip is not None and port is not None:
        return f"http://{base_ip}:{port}/search"
    return base_url

def is_article_page(item: Dict[str, Any]) -> bool:
    title = item.get("title", "")
    url = item.get("url", "").lower()
    snippet = item.get("snippet", "") or item.get("content", "")
    combined_lower = (title + " " + snippet).lower()

    for pattern in non_artical_website:
        if re.search(pattern, url):
            return False

    if re.search(r"(search|explore|genre|tag|category)", url):
        return False

    if len(title.strip()) < 3:
        return False

    strong_negatives = [
        "加入购物车", "立即购买", "购物车", "结账",
        "checkout", "add to cart", "buy now","在线租赁","Online rental"
    ]
    for kw in strong_negatives:
        if kw in combined_lower:
            return False

    positive_signals = 0

    article_url_patterns = [
        r"/news/", r"/article/", r"/post/", r"/p/",
        r"/story/", r"/blog/", r"/entry/", r"/note/",
        r"/columns/", r"/tech/", r"/review/", r"/guide/"
    ]
    for pattern in article_url_patterns:
        if re.search(pattern, url):
            positive_signals += 1
            break

    community_patterns = [
        r"wikipedia\.org", r"baike\.baidu\.com",
        r"zhihu\.com/question/", r"zhuanlan\.zhihu\.com",
        r"reddit\.com/r/.*/comments/",
        r"douban\.com/note/", r"douban\.com/doulist/",
        r"vocus\.cc/", r"medium\.com/", r"substack\.com/"
    ]
    for pattern in community_patterns:
        if re.search(pattern, url):
            positive_signals += 1
            break

    positive_keywords = [
        "评论", "读后感", "推荐", "评测", "教程", "攻略", "经验",
        "分享", "总结", "解析", "盘点", "访谈", "专访", "指南",
        "review", "tutorial", "guide", "analysis", "opinion", "insight"
    ]
    snippet_lower = snippet.lower()
    for kw in positive_keywords:
        if kw in snippet_lower:
            positive_signals += 1
            break

    date_patterns = [
        r"\d{4}-\d{2}-\d{2}",
        r"\d{4}年\d{1,2}月\d{1,2}日",
        r"\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}"
    ]
    for pattern in date_patterns:
        if re.search(pattern, snippet):
            positive_signals += 1
            break

    weak_negatives = [
        "登录", "注册", "首页", "下载", "下载中心",
        "sign in", "login", "register", "download"
    ]
    has_weak_neg = any(kw in combined_lower for kw in weak_negatives)

    if positive_signals >= 1:
        return True

    if has_weak_neg:
        return False

    return True

async def search_single(
    session: AsyncSession,
    url: str,
    query: str,
    min_delay: float,
    max_delay: float,
    timeout: float,
    semaphore: asyncio.Semaphore
) -> Dict[str, Any]:
    result = {
        "success": False,
        "query": query,
        "results": [],
        "error": None
    }
    async with semaphore:
        try:
            delay = random.uniform(min_delay, max_delay)
            await asyncio.sleep(delay)

            resp = await session.get(
                url,
                params={"q": query, "format": "json"},
                timeout=timeout
            )

            if resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}"
                return result

            try:
                data = resp.json()
            except json.JSONDecodeError:
                result["error"] = "响应不是有效 JSON"
                return result

            raw_results = data.get("results", [])
            if not isinstance(raw_results, list):
                result["error"] = "results 字段格式错误，应为列表"
                return result

            result["results"] = raw_results
            result["success"] = True

        except asyncio.TimeoutError:
            result["error"] = "请求超时"
        except Exception as e:
            result["error"] = f"网络或请求异常: {str(e)}"

    return result

def process_raw_results(raw_results: List[Any]) -> Tuple[List[Dict[str, str]], List[str]]:
    combined = []
    errors = []
    seen_urls = set()

    for res in raw_results:
        if isinstance(res, dict) and res.get("success"):
            for item in res.get("results", []):
                url = item.get("url")
                if url and url not in seen_urls:
                    if not is_article_page(item):
                        continue
                    seen_urls.add(url)
                    combined.append({
                        "title": item.get("title", ""),
                        "url": url,
                        "snippet": item.get("content") or item.get("snippet") or ""
                    })
        else:
            if isinstance(res, dict) and res.get("error"):
                errors.append(res["error"])
            elif isinstance(res, Exception):
                errors.append(str(res))
            else:
                errors.append("未知错误")

    unique_errors = list(dict.fromkeys(errors))
    return combined, unique_errors

async def main(
    query: str,
    base_ip: Optional[str] = None,
    port: Optional[Union[int, str]] = None,
    base_url: Optional[str] = None,
    pages: int = DEFAULT_PAGES,
    min_delay: float = float(RANDOM_DELAY_MIN),
    max_delay: float = float(RANDOM_DELAY_MAX)
) -> Dict[str, Any]:
    try:
        min_delay = float(min_delay)
        max_delay = float(max_delay)
        if min_delay > max_delay:
            min_delay, max_delay = 0.6, 2.0
    except (ValueError, TypeError):
        min_delay, max_delay = 0.6, 2.0

    error_msg = validate_params(query, base_ip, port, base_url, pages)
    if error_msg:
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": error_msg,
            "errors": []
        }

    url = build_search_url(base_ip, port, base_url)
    semaphore = asyncio.Semaphore(min(pages, MAX_CONCURRENT_REQUESTS))

    try:
        async with AsyncSession(impersonate="chrome120", timeout=SEARCH_TIMEOUT) as session:
            tasks = [
                search_single(session, url, query, min_delay, max_delay, SEARCH_TIMEOUT, semaphore)
                for _ in range(pages)
            ]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": f"会话创建或请求异常: {e}",
            "errors": []
        }

    combined, errors = process_raw_results(raw_results)

    if combined:
        return {
            "success": True,
            "query": query,
            "results": combined,
            "error": None,
            "errors": errors
        }
    else:
        error_summary = "未获取到结果"
        if errors:
            error_summary += f"（首个错误：{errors[0]}）"
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": error_summary,
            "errors": errors
        }

if __name__ == "__main__":
    result = asyncio.run(main(query="429854.xyz", base_url='https://search-xng-97jd.onrender.com/'))
    print(json.dumps(result, ensure_ascii=False, indent=2))