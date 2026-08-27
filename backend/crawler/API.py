import asyncio
import json
import logging
import os
from typing import Optional, Dict, List, Any

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

# 尝试从 backend.config 导入配置，失败则使用默认值
try:
    from backend.config import (
        SEARCH_TIMEOUT,
        MAX_ARTICLES,
    )
except ImportError:
    SEARCH_TIMEOUT = 10.0
    MAX_ARTICLES = 5

class SearchAPIError(Exception):
    pass

def _get_bing_api_key(api_key: Optional[str] = None) -> Optional[str]:
    """获取 Bing API Key，优先使用传入参数，否则从环境变量读取"""
    if api_key:
        return api_key
    return os.getenv("BING_API_KEY")

async def search_api(
    query: str,
    engine: str = "duckduckgo",
    max_articles: int = MAX_ARTICLES,
    api_key: Optional[str] = None,
    timeout: float = SEARCH_TIMEOUT,
) -> Dict[str, Any]:
    """
    异步搜索引擎 API 封装，支持 DuckDuckGo 和 Bing。
    返回统一 JSON 结构：
    {
        "success": bool,
        "query": str,
        "results": [{"title": str, "url": str, "snippet": str}],
        "error": str | None,
        "errors": list[str]
    }
    """
    # 参数校验
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
    if engine not in ("duckduckgo", "bing"):
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": f"不支持的引擎: {engine}",
            "errors": []
        }

    errors: List[str] = []
    results: List[Dict[str, str]] = []
    seen_urls = set()

    try:
        async with AsyncSession() as session:
            if engine == "duckduckgo":
                results = await _search_duckduckgo(session, query, max_articles, timeout, errors)
            elif engine == "bing":
                bing_key = _get_bing_api_key(api_key)
                if not bing_key:
                    return {
                        "success": False,
                        "query": query,
                        "results": [],
                        "error": "Bing 引擎需要提供 api_key 或设置环境变量 BING_API_KEY",
                        "errors": []
                    }
                results = await _search_bing(session, query, max_articles, bing_key, timeout, errors)
    except Exception as e:
        logger.error(f"搜索引擎请求异常: {e}")
        errors.append(str(e))
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": f"请求过程中发生异常: {e}",
            "errors": errors
        }

    # 去重和限制数量
    final_results = []
    for item in results:
        url = item.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            final_results.append(item)
            if len(final_results) >= max_articles:
                break

    if final_results:
        return {
            "success": True,
            "query": query,
            "results": final_results,
            "error": None,
            "errors": errors
        }
    else:
        return {
            "success": False,
            "query": query,
            "results": [],
            "error": "未获取到任何结果",
            "errors": errors
        }

async def _search_duckduckgo(
    session: AsyncSession,
    query: str,
    max_articles: int,
    timeout: float,
    errors: List[str]
) -> List[Dict[str, str]]:
    url = "https://api.duckduckgo.com/"
    params = {
        "q": query,
        "format": "json",
        "no_html": 1,
        "skip_disambig": 1
    }
    results = []
    try:
        resp = await session.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            errors.append(f"DuckDuckGo HTTP {resp.status_code}")
            return []
        data = resp.json()

        if data.get("AbstractURL"):
            results.append({
                "title": data.get("Heading") or query,
                "url": data["AbstractURL"],
                "snippet": data.get("AbstractText", "")
            })

        def extract_related(topics):
            for topic in topics:
                if "Topics" in topic:
                    extract_related(topic["Topics"])
                elif "FirstURL" in topic and "Text" in topic:
                    results.append({
                        "title": topic["Text"],
                        "url": topic["FirstURL"],
                        "snippet": topic.get("Text", "")
                    })

        related = data.get("RelatedTopics", [])
        extract_related(related)

        unique_results = []
        seen = set()
        for r in results:
            if r["url"] not in seen:
                seen.add(r["url"])
                unique_results.append(r)
                if len(unique_results) >= max_articles:
                    break
        return unique_results

    except Exception as e:
        errors.append(f"DuckDuckGo 请求失败: {e}")
        return []

async def _search_bing(
    session: AsyncSession,
    query: str,
    max_articles: int,
    api_key: str,
    timeout: float,
    errors: List[str]
) -> List[Dict[str, str]]:
    url = "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    params = {
        "q": query,
        "count": max_articles,
        "mkt": "zh-CN"
    }
    results = []
    try:
        resp = await session.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code != 200:
            errors.append(f"Bing HTTP {resp.status_code}")
            return []
        data = resp.json()
        web_pages = data.get("webPages", {}).get("value", [])
        for page in web_pages:
            results.append({
                "title": page.get("name", ""),
                "url": page.get("url", ""),
                "snippet": page.get("snippet", "")
            })
        return results
    except Exception as e:
        errors.append(f"Bing 请求失败: {e}")
        return []

if __name__ == "__main__":
    async def demo():
        # 使用 DuckDuckGo（无需 API key）
        result = await search_api("Python", engine="duckduckgo", max_articles=3)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # 使用 Bing（需要提供 api_key 或设置环境变量 BING_API_KEY）
        # result = await search_api("Python", engine="bing", max_articles=3)
        # print(json.dumps(result, ensure_ascii=False, indent=2))

    asyncio.run(demo())