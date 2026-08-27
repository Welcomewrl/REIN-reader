import asyncio
import re
from typing import Dict, Any
from urllib.parse import urlparse

try:
    from backend.crawler import meta_fetch
    from backend.crawler import search_searxng
    from backend.crawler import API
except ImportError:
    from crawler import meta_fetch
    from crawler import search_searxng
    from crawler import API

PROVIDERS = {
    "meta_fetch": meta_fetch.fetch_multiple_pages,
    "searxng": search_searxng.main,
    "api": API.search_api,
}

# 已知视频网站及其文章栏目路径模式
VIDEO_SITE_ARTICLE_PATTERNS = {
    'bilibili.com': [r'/read/', r'/opus/', r'/column/'],
    'youtube.com': [r'/post/', r'/channel/.*/community'],
    'youku.com': [r'/v_show/.*?\.html'],  # 优酷图文
    'iqiyi.com': [r'/a_'],                # 爱奇艺图文
    'v.qq.com': [r'/x/cover/.*?\.html'],  # 腾讯视频图文
}

# 文章路径关键词（非视频网站）
ARTICLE_PATH_PATTERNS = [
    r'/article/', r'/post/', r'/blog/', r'/news/', r'/story/',
    r'/column/', r'/entry/', r'/archives/', r'/essay/', r'/note/',
    r'/p/',       # 知乎专栏、少数派等
    r'/a/',       # 某些博客
    r'/read/',    # 通用阅读类
]

# 明显非文章路径关键词
NON_ARTICLE_PATH_PATTERNS = [
    r'/video/', r'/watch', r'/embed/', r'/shorts/', r'/live/',
    r'/photo/', r'/image/', r'/gallery/', r'/picture/',
    r'/status/', r'/statuses/', r'/tweet/', r'/thread/', r'/topic/',
    r'/download/', r'\.pdf$', r'\.zip$', r'\.rar$',
    r'/product/', r'/item/', r'/goods/', r'/shop/',
]

def is_article(url: str, title: str = "", snippet: str = "") -> bool:
    """判断搜索结果是否为文章页面"""
    if not url:
        return False
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()

    # 1. 视频网站特殊处理：只保留文章栏目
    for site, patterns in VIDEO_SITE_ARTICLE_PATTERNS.items():
        if site in domain:
            return any(re.search(p, path) for p in patterns)

    # 2. 排除明显的非文章路径
    for pattern in NON_ARTICLE_PATH_PATTERNS:
        if re.search(pattern, path):
            return False

    # 3. 若路径包含文章关键词，直接保留
    for pattern in ARTICLE_PATH_PATTERNS:
        if re.search(pattern, path):
            return True

    # 4. 利用标题和摘要辅助判断
    combined_text = f"{title} {snippet}".lower()
    non_article_keywords = ['视频', '直播', '图集', '图片', '相册', '电影', '电视剧', '音乐', '歌曲', '下载']
    if any(kw in combined_text for kw in non_article_keywords):
        return False

    # 5. 默认保留（无法明确判断时，宁可保留）
    return True

async def main(
    query: str,
    provider: str = "searxng",
    max_articles: int = 10,
    **kwargs
) -> Dict[str, Any]:
    if not query or not isinstance(query, str):
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": "query 必须是非空字符串",
            "errors": []
        }

    if not isinstance(max_articles, int) or max_articles <= 0:
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": "max_articles 必须是正整数",
            "errors": []
        }

    provider_lower = provider.lower()
    if provider_lower not in PROVIDERS:
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": f"不支持的 provider: {provider}",
            "errors": []
        }

    try:
        if provider_lower == "meta_fetch":
            pages = kwargs.get("pages", 3)
            min_delay = kwargs.get("min_delay", 1.0)
            max_delay = kwargs.get("max_delay", 3.0)
            abort_on_warmup_fail = kwargs.get("abort_on_warmup_fail", False)
            check_anti_func = kwargs.get("check_anti_func", None)

            result = await PROVIDERS[provider_lower](
                query=query,
                pages=pages,
                min_delay=min_delay,
                max_delay=max_delay,
                abort_on_warmup_fail=abort_on_warmup_fail,
                check_anti_func=check_anti_func,
                max_articles=max_articles
            )

        elif provider_lower == "searxng":
            base_url = kwargs.get("base_url")
            base_ip = kwargs.get("base_ip")
            port = kwargs.get("port")

            if (base_ip is None or port is None) and base_url is None:
                return {
                    "success": False,
                    "query": query,
                    "results": [],
                    "error": "searxng 需要提供 base_url 或 base_ip+port",
                    "errors": []
                }

            pages = kwargs.get("pages", 3)
            min_delay = kwargs.get("min_delay", 0.6)
            max_delay = kwargs.get("max_delay", 2.0)

            result = await PROVIDERS[provider_lower](
                query=query,
                base_ip=base_ip,
                port=port,
                base_url=base_url,
                pages=pages,
                min_delay=min_delay,
                max_delay=max_delay
            )

        elif provider_lower == "api":
            engine = kwargs.get("engine", "duckduckgo")
            api_key = kwargs.get("api_key")
            timeout = kwargs.get("timeout", 10.0)

            if engine == "bing" and not api_key:
                return {
                    "success": False,
                    "query": query,
                    "results": [],
                    "error": "Bing 引擎需要提供 api_key",
                    "errors": []
                }

            result = await PROVIDERS[provider_lower](
                query=query,
                engine=engine,
                max_articles=max_articles,
                api_key=api_key,
                timeout=timeout
            )

        else:
            result = {
                "success": False,
                "query": query,
                "results": [],
                "error": "未知错误",
                "errors": []
            }

        # 统一过滤文章结果并截断
        if result.get("success") and result.get("results"):
            filtered_results = [
                item for item in result["results"]
                if is_article(item.get("url", ""), item.get("title", ""), item.get("snippet", ""))
            ]
            result["results"] = filtered_results[:max_articles]
            if not result["results"]:
                result["success"] = False
                result["error"] = "过滤后无文章结果"

        return result

    except Exception as e:
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": f"请求处理异常: {e}",
            "errors": [str(e)]
        }

def run_sync(
    query: str,
    provider: str = "searxng",
    max_articles: int = 10,
    **kwargs
) -> Dict[str, Any]:
    return asyncio.run(main(query, provider, max_articles, **kwargs))